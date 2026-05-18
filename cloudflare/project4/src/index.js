const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

const HTML_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
};

const WRITE_TOOLS = new Set(["create_task", "update_task_status", "assign_task", "add_comment", "create_note"]);
const TEXT_SUFFIXES = [".md", ".markdown", ".txt", ".text", ".rst"];

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const accessResponse = requireAccess(request, env, url);
      if (accessResponse) return accessResponse;

      if (request.method === "GET" && url.pathname === "/") return html(INDEX_HTML);
      if (request.method === "GET" && url.pathname === "/debug") return html(DEBUG_HTML);
      if (request.method === "GET" && url.pathname === "/health") return json({ status: "ok" });
      if (request.method === "GET" && url.pathname === "/documents") return json(await listDocuments(env));
      if (request.method === "GET" && url.pathname === "/debug/traces") {
        return json(await listTraces(env, Number(url.searchParams.get("limit") || 50)));
      }
      const traceMatch = url.pathname.match(/^\/debug\/traces\/([^/]+)$/);
      if (request.method === "GET" && traceMatch) return json(await getTrace(env, traceMatch[1]));
      if (request.method === "POST" && url.pathname === "/chat") return json(await chat(env, await request.json()));
      if (request.method === "POST" && url.pathname === "/upload") return json(await upload(env, request));
      const selectMatch = url.pathname.match(/^\/documents\/([^/]+)\/select$/);
      if (request.method === "POST" && selectMatch) return json(await selectDocument(env, request, selectMatch[1]));
      const deleteMatch = url.pathname.match(/^\/documents\/([^/]+)$/);
      if (request.method === "DELETE" && deleteMatch) {
        return json(await deleteDocument(env, deleteMatch[1], url.searchParams.get("session_id")));
      }
      return json({ detail: "Not found" }, 404);
    } catch (error) {
      return json({ detail: error.message || "Internal error" }, error.status || 500);
    }
  },
};

function requireAccess(request, env, url) {
  if (url.pathname === "/health") return null;
  if (env.SKIP_ACCESS_CHECK === "true") return null;
  const allowed = (env.ALLOWED_EMAIL || "").trim().toLowerCase();
  if (!allowed) return null;
  const email = (request.headers.get("Cf-Access-Authenticated-User-Email") || "").trim().toLowerCase();
  if (email === allowed) return null;
  return new Response("Cloudflare Access login required.", { status: 401, headers: { "cache-control": "no-store" } });
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS });
}

function html(body) {
  return new Response(body, { headers: HTML_HEADERS });
}

async function listDocuments(env) {
  const rows = await all(
    env,
    `SELECT d.document_id, d.filename, d.content_type, d.created_at, COUNT(c.chunk_id) AS chunk_count
     FROM documents d
     LEFT JOIN document_chunks c ON c.document_id = d.document_id
     GROUP BY d.document_id, d.filename, d.content_type, d.created_at
     ORDER BY d.created_at DESC, d.filename`,
  );
  return { documents: rows.map((row) => ({ ...row, chunk_count: Number(row.chunk_count || 0) })) };
}

async function upload(env, request) {
  const form = await request.formData();
  const file = form.get("file");
  const sessionId = String(form.get("session_id") || "");
  if (!file || typeof file === "string") throw httpError(400, "Missing file upload.");
  const maxBytes = Number(env.MAX_UPLOAD_BYTES || 3145728);
  if (file.size > maxBytes) throw httpError(413, `File is too large. Limit is ${Math.floor(maxBytes / 1024 / 1024)} MB.`);
  const filename = file.name || "uploaded.txt";
  if (!isSupportedTextFile(filename)) throw httpError(415, "For the Cloudflare v1 deploy, upload markdown, text, or reStructuredText files.");

  const content = new TextDecoder().decode(await file.arrayBuffer());
  const chunks = chunkText(content);
  const maxChunks = Number(env.MAX_UPLOAD_CHUNKS || 40);
  if (chunks.length > maxChunks) {
    throw httpError(413, `File creates too many chunks for this deployment. Limit is ${maxChunks} chunks.`);
  }
  await enforceDailyBudget(env, request, "upload", Number(env.DAILY_UPLOAD_LIMIT || 20));
  await enforceDailyBudget(env, request, "openai", Number(env.DAILY_GLOBAL_OPENAI_LIMIT || 100), chunks.length);
  const documentId = crypto.randomUUID();
  await run(env, "INSERT INTO documents(document_id, filename, content_type) VALUES (?, ?, ?)", [
    documentId,
    filename,
    file.type || "text/plain",
  ]);
  let reindexed = 0;
  for (let index = 0; index < chunks.length; index += 1) {
    const embedding = await embed(env, chunks[index]);
    await run(
      env,
      "INSERT INTO document_chunks(chunk_id, document_id, chunk_index, text, embedding_json) VALUES (?, ?, ?, ?, ?)",
      [`${documentId}:${index}`, documentId, index, chunks[index], JSON.stringify(embedding)],
    );
    reindexed += 1;
  }

  let context = null;
  if (sessionId) {
    context = await loadSession(env, sessionId);
    context.current_document_id = documentId;
    context.current_document_filename = filename;
    appendTurn(context, "assistant", `Uploaded ${filename} and selected it as the current document.`);
    await saveSession(env, context);
  }
  return { document_id: documentId, filename, chunk_count: chunks.length, reindexed_chunk_count: reindexed, context };
}

async function selectDocument(env, request, documentId) {
  const form = await request.formData();
  const sessionId = String(form.get("session_id") || "");
  if (!sessionId) throw httpError(400, "session_id is required.");
  const document = await first(
    env,
    `SELECT d.document_id, d.filename, d.content_type, d.created_at, COUNT(c.chunk_id) AS chunk_count
     FROM documents d
     LEFT JOIN document_chunks c ON c.document_id = d.document_id
     WHERE d.document_id = ?
     GROUP BY d.document_id, d.filename, d.content_type, d.created_at`,
    [documentId],
  );
  if (!document) throw httpError(404, `document ${documentId} does not exist`);
  const context = await loadSession(env, sessionId);
  context.current_document_id = document.document_id;
  context.current_document_filename = document.filename;
  appendTurn(context, "assistant", `Selected ${document.filename} as the current document.`);
  await saveSession(env, context);
  return { document: { ...document, chunk_count: Number(document.chunk_count || 0) }, context };
}

async function deleteDocument(env, documentId, sessionId) {
  const existing = await first(env, "SELECT filename FROM documents WHERE document_id = ?", [documentId]);
  if (!existing) return { document_id: documentId, deleted: false, reindexed_chunk_count: 0, context: null };
  await run(env, "DELETE FROM document_chunks WHERE document_id = ?", [documentId]);
  await run(env, "DELETE FROM documents WHERE document_id = ?", [documentId]);
  let context = null;
  if (sessionId) {
    context = await loadSession(env, sessionId);
    if (context.current_document_id === documentId) {
      context.current_document_id = null;
      context.current_document_filename = null;
      appendTurn(context, "assistant", "Deleted the selected document and cleared the current file.");
    }
    await saveSession(env, context);
  }
  return { document_id: documentId, deleted: true, reindexed_chunk_count: 0, context };
}

async function chat(env, request) {
  if (!request?.session_id || !request?.message) throw httpError(400, "session_id and message are required.");
  await enforceDailyBudget(env, request, "chat", Number(env.DAILY_CHAT_LIMIT || 100));
  if (!request.confirm_action_id) {
    await enforceDailyBudget(env, request, "openai", Number(env.DAILY_GLOBAL_OPENAI_LIMIT || 100), 3);
  }
  const context = await loadSession(env, request.session_id);
  appendTurn(context, "user", request.message);

  let response;
  if (request.confirm_action_id) {
    response = await confirmAction(env, request, context);
  } else {
    response = await answer(env, request, context);
  }
  appendTurn(context, "assistant", response.response);
  response.context = context;
  await saveSession(env, context);
  response.trace_id = await appendTrace(env, request.message, response);
  return response;
}

async function answer(env, request, context) {
  const text = request.message.trim().toLowerCase();
  if (isGreeting(text)) {
    return makeResponse(request, context, "clarify", "Hi. Upload a file or ask me about tasks, project data, or a task action.", {
      reasoning: "Simple greeting handled without a model call.",
    });
  }
  if (asksAboutFiles(text) && !context.current_document_id) {
    return makeResponse(request, context, "clarify", "I do not have an uploaded file in this workspace yet. Upload a markdown or text file first, then ask about it.", {
      reasoning: "The user asked about a file before any file was uploaded.",
    });
  }

  try {
    if (shouldAnswerCurrentDocument(text, context)) {
      return answerFromFiles(env, request, context, "The user asked about the current session document.", request.message);
    }
    const decision = await parseWithOpenAI(env, "copilot_orchestrator", ORCHESTRATOR_SCHEMA, ORCHESTRATOR_PROMPT, {
      message: request.message,
      context,
    });
    if (decision.route === "file_retrieval") return answerFromFiles(env, request, context, decision.reasoning, decision.search_query || request.message);
    if (decision.route === "sql_query") return answerFromSql(env, request, context, decision.reasoning);
    if (decision.route === "api_tool") return handleTool(env, request, context, decision);
    if (decision.route === "context") return answerFromContext(request, context, decision.reasoning);
    return makeResponse(request, context, "clarify", decision.clarification_question || "Should I search files, query tasks, or perform a task action?", {
      reasoning: decision.reasoning || "The route was ambiguous.",
    });
  } catch (error) {
    return makeResponse(request, context, "clarify", "I could not complete the model call. Check OPENAI_API_KEY, OPENAI_MODEL, and network access, then try again.", {
      reasoning: `Model/API call failed: ${error.message}`,
    });
  }
}

async function answerFromFiles(env, request, context, reasoning, query) {
  if (!context.current_document_id) {
    return makeResponse(request, context, "clarify", "I do not have a file selected for this session yet. Upload a markdown or text file first, then ask about it.", {
      reasoning,
    });
  }
  const chunks = await searchChunks(env, query, context.current_document_id);
  if (!chunks.length) {
    return makeResponse(request, context, "file_retrieval", "I could not find relevant uploaded file content for that question.", {
      reasoning,
      selected_data_source: "uploaded_files",
      citations: [],
    });
  }
  const answer = await parseWithOpenAI(env, "file_qa_agent", FILE_ANSWER_SCHEMA, FILE_QA_PROMPT, {
    message: request.message,
    chunks,
    context,
  });
  const citedIds = new Set(answer.cited_chunk_ids || []);
  const citations = chunks
    .filter((chunk) => !citedIds.size || citedIds.has(chunk.chunk_id))
    .map(({ text: _text, ...citation }) => citation);
  return makeResponse(request, context, "file_retrieval", answer.answer, {
    reasoning,
    selected_data_source: "uploaded_files",
    citations,
  });
}

async function answerFromSql(env, request, context, reasoning) {
  const plan = await parseWithOpenAI(env, "sql_agent", SQL_PLAN_SCHEMA, SQL_PROMPT, {
    message: request.message,
    schema: SCHEMA_DESCRIPTION,
    context,
  });
  try {
    const safeSql = validateReadOnlySql(plan.sql);
    const rows = await all(env, safeSql);
    const columns = rows.length ? Object.keys(rows[0]) : [];
    return makeResponse(request, context, "sql_query", explainRows(rows), {
      reasoning,
      selected_data_source: "task_database",
      generated_sql: safeSql,
      sql_result: { sql: safeSql, columns, rows, explanation: plan.explanation || "Read-only SQL generated from the task database schema." },
    });
  } catch (error) {
    return makeResponse(request, context, "sql_query", `I refused to run the generated SQL because it was not read-only: ${error.message}`, {
      reasoning,
      selected_data_source: "task_database",
      generated_sql: plan.sql,
    });
  }
}

async function handleTool(env, request, context, decision) {
  const toolCall = await parseWithOpenAI(env, "tool_agent", TOOL_CALL_SCHEMA, TOOL_PROMPT, {
    message: request.message,
    tool_name: decision.tool_name,
    tool_args: decision.tool_args || {},
    context,
  });
  if (WRITE_TOOLS.has(toolCall.name) || toolCall.requires_confirmation) {
    const actionId = crypto.randomUUID();
    const pending = { action_id: actionId, tool_call: toolCall, preview: previewTool(toolCall) };
    context.pending_actions[actionId] = toolCall;
    const workflowId = await createWorkflow(env, context, actionId, toolCall, pending.preview);
    return makeResponse(request, context, "api_tool", `I can do that, but need confirmation first. Proposed action: ${pending.preview}`, {
      reasoning: decision.reasoning,
      selected_data_source: "project_api",
      tool_name: toolCall.name,
      workflow_id: workflowId,
      tool_call: toolCall,
      pending_action: pending,
    });
  }
  const result = await executeTool(env, toolCall);
  updateContextFromTool(context, result);
  return makeResponse(request, context, "api_tool", explainToolResult(result), {
    reasoning: decision.reasoning,
    selected_data_source: "project_api",
    tool_name: toolCall.name,
    tool_call: toolCall,
    tool_result: result,
  });
}

async function confirmAction(env, request, context) {
  const toolCall = context.pending_actions[request.confirm_action_id];
  if (!toolCall) {
    const result = { name: "search_tasks", ok: false, error: "No pending action found for that confirmation id." };
    return makeResponse(request, context, "api_tool", result.error, {
      reasoning: "User attempted to confirm a missing pending action.",
      selected_data_source: "project_api",
      tool_result: result,
    });
  }
  delete context.pending_actions[request.confirm_action_id];
  const result = await executeTool(env, toolCall);
  const workflowId = await finishWorkflow(env, context, request.confirm_action_id, result);
  updateContextFromTool(context, result);
  return makeResponse(request, context, "api_tool", explainToolResult(result), {
    reasoning: "User confirmed a pending state-changing API action.",
    selected_data_source: "project_api",
    tool_name: toolCall.name,
    workflow_id: workflowId,
    tool_call: toolCall,
    tool_result: result,
  });
}

function answerFromContext(request, context, reasoning) {
  return makeResponse(
    request,
    context,
    "context",
    `Current project: ${context.current_project_id || "none"}. Current task: ${context.current_task_id || "none"}. Current document: ${context.current_document_filename || context.current_document_id || "none"}. Current note: ${context.current_note_id || "none"}. Current workflow: ${context.current_workflow_id || "none"}.`,
    { reasoning, selected_data_source: "session_context" },
  );
}

function makeResponse(request, context, route, response, extras = {}) {
  return {
    session_id: request.session_id,
    response,
    route,
    trace_id: null,
    citations: extras.citations || [],
    generated_sql: extras.generated_sql || null,
    sql_result: extras.sql_result || null,
    tool_call: extras.tool_call || null,
    tool_result: extras.tool_result || null,
    pending_action: extras.pending_action || null,
    context,
    decision_log: {
      route,
      reasoning: extras.reasoning || "",
      selected_data_source: extras.selected_data_source || null,
      generated_sql: extras.generated_sql || null,
      tool_name: extras.tool_name || null,
      workflow_id: extras.workflow_id || null,
      created_at: new Date().toISOString(),
    },
  };
}

async function executeTool(env, toolCall) {
  const args = toolCall.args || {};
  try {
    if (toolCall.name === "create_task") {
      const projectId = requiredInt(args, "project_id");
      await requireExists(env, "projects", "project_id", projectId);
      const result = await run(env, "INSERT INTO tasks(project_id, title, description, status, assignee_id) VALUES (?, ?, ?, 'open', ?)", [
        projectId,
        requiredStr(args, "title"),
        args.description || "",
        args.assignee_id || null,
      ]);
      return { name: "create_task", ok: true, result: { task_id: result.meta.last_row_id, project_id: projectId } };
    }
    if (toolCall.name === "update_task_status") {
      const taskId = requiredInt(args, "task_id");
      const status = requiredStr(args, "status");
      if (!["open", "in_progress", "blocked", "done"].includes(status)) throw new Error("status must be open, in_progress, blocked, or done");
      const existing = await first(env, "SELECT status FROM tasks WHERE task_id = ?", [taskId]);
      if (!existing) throw new Error(`task ${taskId} does not exist`);
      await run(env, "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", [status, taskId]);
      await run(env, "INSERT INTO status_history(task_id, old_status, new_status, changed_by) VALUES (?, ?, ?, ?)", [
        taskId,
        existing.status,
        status,
        args.changed_by || 1,
      ]);
      return { name: "update_task_status", ok: true, result: { task_id: taskId, status } };
    }
    if (toolCall.name === "assign_task") {
      const taskId = requiredInt(args, "task_id");
      const userId = requiredInt(args, "user_id");
      await requireExists(env, "tasks", "task_id", taskId);
      await requireExists(env, "users", "user_id", userId);
      await run(env, "UPDATE tasks SET assignee_id = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", [userId, taskId]);
      return { name: "assign_task", ok: true, result: { task_id: taskId, user_id: userId } };
    }
    if (toolCall.name === "add_comment") {
      const taskId = requiredInt(args, "task_id");
      const userId = Number(args.user_id || 1);
      await requireExists(env, "tasks", "task_id", taskId);
      await requireExists(env, "users", "user_id", userId);
      const result = await run(env, "INSERT INTO comments(task_id, user_id, body) VALUES (?, ?, ?)", [taskId, userId, requiredStr(args, "body")]);
      return { name: "add_comment", ok: true, result: { comment_id: result.meta.last_row_id, task_id: taskId } };
    }
    if (toolCall.name === "search_tasks") {
      const query = `%${String(args.query || "").toLowerCase()}%`;
      const rows = await all(
        env,
        `SELECT task_id, title, status, project_id, assignee_id
         FROM tasks
         WHERE lower(title) LIKE ? OR lower(description) LIKE ? OR lower(status) LIKE ?
         ORDER BY task_id`,
        [query, query, query],
      );
      return { name: "search_tasks", ok: true, result: rows };
    }
    if (toolCall.name === "create_note") {
      const result = await run(env, "INSERT INTO personal_notes(title, body, source_document_id, source_filename) VALUES (?, ?, ?, ?)", [
        requiredStr(args, "title"),
        requiredStr(args, "body"),
        args.source_document_id || null,
        args.source_filename || null,
      ]);
      return { name: "create_note", ok: true, result: { note_id: result.meta.last_row_id, title: args.title, source_document_id: args.source_document_id || null } };
    }
    if (toolCall.name === "search_notes") {
      const query = `%${String(args.query || "").toLowerCase()}%`;
      const rows = await all(
        env,
        `SELECT note_id, title, body, source_filename, created_at
         FROM personal_notes
         WHERE lower(title) LIKE ? OR lower(body) LIKE ? OR lower(source_filename) LIKE ?
         ORDER BY note_id`,
        [query, query, query],
      );
      return { name: "search_notes", ok: true, result: rows };
    }
    return { name: toolCall.name, ok: false, error: `Unsupported tool: ${toolCall.name}` };
  } catch (error) {
    return { name: toolCall.name, ok: false, error: error.message };
  }
}

async function createWorkflow(env, context, actionId, toolCall, preview) {
  const workflowId = `wf_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
  const args = toolCall.args || {};
  await run(
    env,
    `INSERT INTO productivity_workflows(workflow_id, session_id, workflow_type, status, pending_action_id, source_document_id, source_note_id, summary)
     VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?, ?)`,
    [
      workflowId,
      context.session_id,
      workflowType(toolCall),
      actionId,
      args.source_document_id || context.current_document_id || null,
      args.note_id || context.current_note_id || null,
      preview,
    ],
  );
  await run(env, "INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json) VALUES (?, 1, 'proposed_tool_action', 'awaiting_review', ?)", [
    workflowId,
    JSON.stringify({ tool_name: toolCall.name, args, preview }),
  ]);
  context.current_workflow_id = workflowId;
  return workflowId;
}

async function finishWorkflow(env, context, actionId, result) {
  const row = await first(
    env,
    "SELECT workflow_id FROM productivity_workflows WHERE session_id = ? AND pending_action_id = ? AND status = 'awaiting_review' ORDER BY created_at DESC LIMIT 1",
    [context.session_id, actionId],
  );
  if (!row) return null;
  const status = result.ok ? "completed" : "failed";
  await run(env, "UPDATE productivity_workflows SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE workflow_id = ?", [status, row.workflow_id]);
  await run(env, "INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json) VALUES (?, 2, 'confirmed_tool_execution', ?, ?)", [
    row.workflow_id,
    status,
    JSON.stringify(result),
  ]);
  context.current_workflow_id = row.workflow_id;
  return row.workflow_id;
}

async function searchChunks(env, query, documentId) {
  const queryEmbedding = await embed(env, query);
  const rows = await all(
    env,
    `SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.embedding_json, d.filename
     FROM document_chunks c
     JOIN documents d ON d.document_id = c.document_id
     WHERE c.document_id = ?`,
    [documentId],
  );
  return rows
    .map((row) => {
      const score = cosineSimilarity(queryEmbedding, JSON.parse(row.embedding_json || "[]"));
      return {
        document_id: row.document_id,
        filename: row.filename,
        chunk_id: row.chunk_id,
        chunk_index: row.chunk_index,
        score,
        quote: shortQuote(row.text),
        text: row.text,
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 4);
}

async function embed(env, text) {
  const response = await fetch("https://api.openai.com/v1/embeddings", {
    method: "POST",
    headers: openAIHeaders(env),
    body: JSON.stringify({ model: env.OPENAI_EMBEDDING_MODEL || "text-embedding-3-small", input: text }),
  });
  if (!response.ok) throw new Error(`OpenAI embeddings failed: ${response.status} ${await response.text()}`);
  const data = await response.json();
  return data.data?.[0]?.embedding || [];
}

async function parseWithOpenAI(env, taskName, schema, systemPrompt, payload) {
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: openAIHeaders(env),
    body: JSON.stringify({
      model: env.OPENAI_MODEL || "gpt-5.5",
      input: [
        { role: "system", content: systemPrompt },
        { role: "user", content: `Task: ${taskName}\n\n${JSON.stringify(payload)}` },
      ],
      text: {
        format: {
          type: "json_schema",
          name: taskName,
          schema,
          strict: true,
        },
      },
    }),
  });
  if (!response.ok) throw new Error(`OpenAI responses failed: ${response.status} ${await response.text()}`);
  const data = await response.json();
  const output = data.output_text || extractOutputText(data);
  if (!output) throw new Error("OpenAI response did not include output text.");
  return JSON.parse(output);
}

function openAIHeaders(env) {
  if (!env.OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not configured.");
  return {
    authorization: `Bearer ${env.OPENAI_API_KEY}`,
    "content-type": "application/json",
  };
}

function extractOutputText(data) {
  const parts = [];
  for (const output of data.output || []) {
    for (const item of output.content || []) {
      if (item.type === "output_text" && item.text) parts.push(item.text);
      if (item.text) parts.push(item.text);
    }
  }
  return parts.join("\n").trim();
}

async function loadSession(env, sessionId) {
  const row = await first(env, "SELECT payload_json FROM sessions WHERE session_id = ?", [sessionId]);
  if (row) return JSON.parse(row.payload_json);
  return {
    session_id: sessionId,
    current_project_id: null,
    current_task_id: null,
    current_document_id: null,
    current_document_filename: null,
    current_note_id: null,
    current_workflow_id: null,
    pending_actions: {},
    history: [],
    updated_at: new Date().toISOString(),
  };
}

async function saveSession(env, context) {
  context.updated_at = new Date().toISOString();
  await run(
    env,
    `INSERT INTO sessions(session_id, payload_json, updated_at)
     VALUES (?, ?, CURRENT_TIMESTAMP)
     ON CONFLICT(session_id) DO UPDATE SET payload_json = excluded.payload_json, updated_at = CURRENT_TIMESTAMP`,
    [context.session_id, JSON.stringify(context)],
  );
}

function appendTurn(context, role, content) {
  context.history.push({ role, content, created_at: new Date().toISOString() });
  if (context.history.length > 12) context.history = context.history.slice(-12);
}

async function appendTrace(env, userMessage, response) {
  const traceId = `trace_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
  const spans = [
    {
      span_id: `${traceId}_1`,
      trace_id: traceId,
      parent_span_id: null,
      ordinal: 1,
      actor_type: "user",
      actor_name: "user",
      event_type: "message",
      title: "User message",
      status: "received",
      input_summary: userMessage,
      output_summary: null,
      metadata: {},
      created_at: new Date().toISOString(),
    },
    {
      span_id: `${traceId}_2`,
      trace_id: traceId,
      parent_span_id: `${traceId}_1`,
      ordinal: 2,
      actor_type: "response",
      actor_name: "assistant",
      event_type: "final_response",
      title: response.route,
      status: response.route,
      input_summary: response.decision_log.reasoning,
      output_summary: response.response,
      metadata: { generated_sql: response.generated_sql, tool_name: response.decision_log.tool_name },
      created_at: new Date().toISOString(),
    },
  ];
  await run(
    env,
    "INSERT INTO trace_turns(trace_id, session_id, user_message, final_response, route, status, span_count, spans_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    [traceId, response.session_id, userMessage, response.response, response.route, response.route, spans.length, JSON.stringify(spans)],
  );
  return traceId;
}

async function listTraces(env, limit) {
  const rows = await all(
    env,
    `SELECT trace_id, session_id, route, status, user_message, final_response, span_count, created_at
     FROM trace_turns
     ORDER BY created_at DESC
     LIMIT ?`,
    [Math.min(Math.max(limit || 50, 1), 100)],
  );
  return { traces: rows };
}

async function getTrace(env, traceId) {
  const row = await first(
    env,
    `SELECT trace_id, session_id, route, status, user_message, final_response, span_count, spans_json, created_at
     FROM trace_turns
     WHERE trace_id = ?`,
    [traceId],
  );
  if (!row) throw httpError(404, `trace ${traceId} does not exist`);
  const spans = JSON.parse(row.spans_json || "[]");
  delete row.spans_json;
  return { ...row, spans };
}

async function all(env, sql, params = []) {
  const statement = env.DB.prepare(sql);
  const result = await (params.length ? statement.bind(...params) : statement).all();
  return result.results || [];
}

async function first(env, sql, params = []) {
  const statement = env.DB.prepare(sql);
  return await (params.length ? statement.bind(...params) : statement).first();
}

async function run(env, sql, params = []) {
  const statement = env.DB.prepare(sql);
  return await (params.length ? statement.bind(...params) : statement).run();
}

async function enforceDailyBudget(env, request, bucket, limit, amount = 1) {
  if (!Number.isFinite(limit) || limit <= 0) return;
  const increment = Math.max(1, Math.ceil(Number(amount) || 1));
  const date = new Date().toISOString().slice(0, 10);
  const identity = currentIdentity(request, env);
  const keys = [`usage:${date}:global:${bucket}`, `usage:${date}:user:${identity}:${bucket}`];
  for (const key of keys) {
    const existing = await first(env, "SELECT count FROM usage_counters WHERE counter_key = ?", [key]);
    const count = Number(existing?.count || 0);
    if (count + increment > limit) {
      throw httpError(429, `Daily ${bucket} limit reached for this deployment. Try again tomorrow.`);
    }
  }
  for (const key of keys) {
    await run(
      env,
      `INSERT INTO usage_counters(counter_key, count, updated_at)
       VALUES (?, ?, CURRENT_TIMESTAMP)
       ON CONFLICT(counter_key) DO UPDATE SET count = count + excluded.count, updated_at = CURRENT_TIMESTAMP`,
      [key, increment],
    );
  }
}

function currentIdentity(request, env) {
  return (
    request.headers.get("Cf-Access-Authenticated-User-Email") ||
    env.ALLOWED_EMAIL ||
    "anonymous"
  )
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.@-]/g, "_");
}

async function requireExists(env, table, column, value) {
  const row = await first(env, `SELECT 1 AS found FROM ${table} WHERE ${column} = ?`, [value]);
  if (!row) throw new Error(`${table}.${column}=${value} does not exist`);
}

function validateReadOnlySql(sql) {
  const cleaned = String(sql || "").trim().replace(/;+\s*$/, "");
  if (!/^select\b/i.test(cleaned)) throw new Error("only SELECT statements are allowed");
  if (/[;]/.test(cleaned)) throw new Error("multiple statements are not allowed");
  if (/\b(insert|update|delete|drop|alter|create|replace|pragma|attach|detach|vacuum)\b/i.test(cleaned)) {
    throw new Error("write/admin SQL keywords are blocked");
  }
  return cleaned;
}

function isSupportedTextFile(filename) {
  const lower = filename.toLowerCase();
  return TEXT_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

function chunkText(text, chunkSize = 900, overlap = 120) {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return ["Empty uploaded file."];
  const chunks = [];
  let start = 0;
  while (start < normalized.length) {
    const end = Math.min(start + chunkSize, normalized.length);
    chunks.push(normalized.slice(start, end).trim());
    if (end >= normalized.length) break;
    start = Math.max(0, end - overlap);
  }
  return chunks;
}

function cosineSimilarity(left, right) {
  if (!left.length || left.length !== right.length) return 0;
  let numerator = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let i = 0; i < left.length; i += 1) {
    numerator += left[i] * right[i];
    leftNorm += left[i] * left[i];
    rightNorm += right[i] * right[i];
  }
  if (!leftNorm || !rightNorm) return 0;
  return numerator / (Math.sqrt(leftNorm) * Math.sqrt(rightNorm));
}

function shortQuote(text) {
  const compact = String(text || "").replace(/\s+/g, " ").trim();
  return compact.length > 220 ? `${compact.slice(0, 217)}...` : compact;
}

function requiredInt(args, key) {
  if (args[key] === undefined || args[key] === null) throw new Error(`missing required arg: ${key}`);
  return Number(args[key]);
}

function requiredStr(args, key) {
  const value = String(args[key] || "").trim();
  if (!value) throw new Error(`missing required arg: ${key}`);
  return value;
}

function previewTool(toolCall) {
  const args = toolCall.args || {};
  if (toolCall.name === "create_task") return `Create task '${args.title}' in project ${args.project_id}.`;
  if (toolCall.name === "update_task_status") return `Update task ${args.task_id} to status '${args.status}'.`;
  if (toolCall.name === "assign_task") return `Assign task ${args.task_id} to user ${args.user_id}.`;
  if (toolCall.name === "add_comment") return `Add a comment to task ${args.task_id}: ${args.body}`;
  if (toolCall.name === "create_note") return `Create personal note '${args.title}'.`;
  if (toolCall.name === "search_notes") return `Search personal notes for ${JSON.stringify(args.query)}.`;
  return `Search tasks for ${JSON.stringify(args.query)}.`;
}

function workflowType(toolCall) {
  if (toolCall.name === "create_note") return "capture_personal_note";
  if (toolCall.name === "create_task" && (toolCall.args?.source_document_id || toolCall.args?.note_id || toolCall.args?.source_filename)) {
    return "document_or_note_to_task";
  }
  return `${toolCall.name}_workflow`;
}

function updateContextFromTool(context, result) {
  if (!result.ok || !result.result || Array.isArray(result.result)) return;
  if (result.result.project_id) context.current_project_id = Number(result.result.project_id);
  if (result.result.task_id) context.current_task_id = Number(result.result.task_id);
  if (result.result.note_id) context.current_note_id = Number(result.result.note_id);
}

function explainRows(rows) {
  if (!rows.length) return "The read-only query returned no rows.";
  if (rows.length === 1 && Object.keys(rows[0]).length === 1) {
    const [key, value] = Object.entries(rows[0])[0];
    return `The query returned ${key}: ${value}.`;
  }
  const preview = rows
    .slice(0, 5)
    .map((row) => Object.entries(row).map(([key, value]) => `${key}=${value}`).join(", "))
    .join("; ");
  return `The query returned ${rows.length} rows: ${preview}.${rows.length > 5 ? ` Showing 5 of ${rows.length} rows.` : ""}`;
}

function explainToolResult(result) {
  if (!result.ok) return `The tool call failed: ${result.error}`;
  return `Tool ${result.name} executed successfully: ${JSON.stringify(result.result)}`;
}

function isGreeting(text) {
  return ["hi", "hello", "hey"].includes(text);
}

function asksAboutFiles(text) {
  return /\b(file|document|uploaded|pdf|brief|requirements)\b/.test(text);
}

function shouldAnswerCurrentDocument(text, context) {
  return Boolean(context.current_document_id && /\b(this|current|file|document|uploaded|it)\b/.test(text));
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

const SCHEMA_DESCRIPTION = `
users(user_id INTEGER, name TEXT, email TEXT)
projects(project_id INTEGER, name TEXT, description TEXT, created_at TEXT)
tasks(task_id INTEGER, project_id INTEGER, title TEXT, description TEXT, status TEXT, assignee_id INTEGER, created_at TEXT, updated_at TEXT)
comments(comment_id INTEGER, task_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT)
status_history(history_id INTEGER, task_id INTEGER, old_status TEXT, new_status TEXT, changed_by INTEGER, changed_at TEXT)
personal_notes(note_id INTEGER, title TEXT, body TEXT, source_document_id TEXT, source_filename TEXT, created_at TEXT, updated_at TEXT)
productivity_workflows(workflow_id TEXT, session_id TEXT, workflow_type TEXT, status TEXT, pending_action_id TEXT, source_document_id TEXT, source_note_id INTEGER, summary TEXT)
workflow_steps(step_id INTEGER, workflow_id TEXT, step_index INTEGER, name TEXT, status TEXT, output_json TEXT)
`;

const ORCHESTRATOR_PROMPT = "Route a personal productivity copilot request to one path: context, file_retrieval, sql_query, api_tool, or clarify. Use file_retrieval for uploaded file questions, sql_query for read-only structured database questions, api_tool for task or personal-note actions, context for session state questions, and clarify when ambiguous.";
const SQL_PROMPT = "Generate one read-only SQLite SELECT query for the user's question. Use only the provided schema. Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, PRAGMA, or other write/admin SQL.";
const TOOL_PROMPT = "Propose one productivity tool call. State-changing tools require confirmation. Use create_task, update_task_status, assign_task, add_comment, search_tasks, create_note, or search_notes.";
const FILE_QA_PROMPT = "Answer using only retrieved file chunks. Cite chunk ids that support the answer. If the chunks do not answer the question, say that the uploaded files do not contain enough evidence.";

const TOOL_ARGS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    project_id: { type: ["integer", "null"] },
    title: { type: ["string", "null"] },
    description: { type: ["string", "null"] },
    task_id: { type: ["integer", "null"] },
    status: { type: ["string", "null"], enum: ["open", "in_progress", "blocked", "done", null] },
    user_id: { type: ["integer", "null"] },
    assignee_id: { type: ["integer", "null"] },
    changed_by: { type: ["integer", "null"] },
    body: { type: ["string", "null"] },
    query: { type: ["string", "null"] },
    note_id: { type: ["integer", "null"] },
    source_document_id: { type: ["string", "null"] },
    source_filename: { type: ["string", "null"] },
    workflow_id: { type: ["string", "null"] },
  },
  required: [
    "project_id",
    "title",
    "description",
    "task_id",
    "status",
    "user_id",
    "assignee_id",
    "changed_by",
    "body",
    "query",
    "note_id",
    "source_document_id",
    "source_filename",
    "workflow_id",
  ],
};

const ORCHESTRATOR_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    route: { type: "string", enum: ["context", "file_retrieval", "sql_query", "api_tool", "clarify"] },
    reasoning: { type: "string" },
    search_query: { type: ["string", "null"] },
    tool_name: { type: ["string", "null"], enum: ["create_task", "update_task_status", "assign_task", "add_comment", "search_tasks", "create_note", "search_notes", null] },
    tool_args: TOOL_ARGS_SCHEMA,
    clarification_question: { type: ["string", "null"] },
  },
  required: ["route", "reasoning", "search_query", "tool_name", "tool_args", "clarification_question"],
};

const SQL_PLAN_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    sql: { type: "string" },
    explanation: { type: "string" },
  },
  required: ["sql", "explanation"],
};

const TOOL_CALL_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    name: { type: "string", enum: ["create_task", "update_task_status", "assign_task", "add_comment", "search_tasks", "create_note", "search_notes"] },
    args: TOOL_ARGS_SCHEMA,
    requires_confirmation: { type: "boolean" },
    reason: { type: "string" },
  },
  required: ["name", "args", "requires_confirmation", "reason"],
};

const FILE_ANSWER_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    answer: { type: "string" },
    cited_chunk_ids: { type: "array", items: { type: "string" } },
  },
  required: ["answer", "cited_chunk_ids"],
};

const INDEX_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agentic Project Copilot</title>
  <style>
    :root { color-scheme: light; --bg:#f7f8fa; --panel:#fff; --ink:#1f2933; --muted:#64748b; --border:#d7dde5; --accent:#2563eb; --soft:#e8f0ff; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--ink); }
    main { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:16px; min-height:100vh; padding:16px; }
    .chat, aside { background:var(--panel); border:1px solid var(--border); border-radius:8px; min-width:0; }
    .chat { display:grid; grid-template-rows:auto 1fr auto; overflow:hidden; }
    header, aside header { padding:14px 16px; border-bottom:1px solid var(--border); font-weight:650; }
    header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
    a { color:var(--accent); font-size:13px; text-decoration:none; font-weight:650; white-space:nowrap; }
    #messages { padding:16px; overflow:auto; display:flex; flex-direction:column; gap:12px; }
    .msg { max-width:820px; border:1px solid var(--border); border-radius:8px; padding:12px; white-space:pre-wrap; line-height:1.45; }
    .user { align-self:flex-end; background:var(--soft); }
    .assistant { align-self:flex-start; background:#fff; }
    form { display:flex; gap:8px; padding:12px; border-top:1px solid var(--border); }
    input[type="text"] { flex:1; border:1px solid var(--border); border-radius:6px; padding:10px 12px; font-size:15px; min-width:0; }
    button { border:1px solid var(--accent); background:var(--accent); color:white; border-radius:6px; padding:9px 12px; cursor:pointer; font-weight:600; }
    button.secondary { background:#fff; color:var(--accent); }
    button.danger { border-color:#b91c1c; background:#fff; color:#b91c1c; }
    button:disabled { cursor:wait; opacity:.7; }
    aside { padding-bottom:12px; }
    aside section { padding:12px 14px; border-bottom:1px solid var(--border); }
    h2 { font-size:13px; margin:0 0 8px; color:var(--muted); text-transform:uppercase; }
    pre { background:#0f172a; color:#e2e8f0; border-radius:6px; padding:10px; overflow:auto; font-size:12px; }
    .small { color:var(--muted); font-size:13px; }
    .state-line { color:var(--ink); font-size:14px; line-height:1.4; margin:8px 0 0; overflow-wrap:anywhere; }
    .document-list { display:grid; gap:8px; margin-top:8px; }
    .document-row { border:1px solid var(--border); border-radius:6px; padding:8px; display:grid; gap:6px; }
    .document-title { font-size:13px; font-weight:650; overflow-wrap:anywhere; }
    .document-actions { display:flex; gap:6px; flex-wrap:wrap; }
    .document-actions button { padding:5px 8px; font-size:12px; }
    .citation { border-left:3px solid var(--accent); padding-left:8px; margin:6px 0; font-size:13px; }
    @media (max-width:860px) { main { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <main>
    <section class="chat">
      <header><span>Agentic Project Copilot</span><a href="/debug">Debug Traces</a></header>
      <div id="messages"></div>
      <form id="chat-form"><input id="message" type="text" autocomplete="off" placeholder="Ask about files, tasks, or actions"><button type="submit">Send</button></form>
    </section>
    <aside>
      <header>Runtime State</header>
      <section><h2>Upload</h2><input id="file" type="file" accept=".md,.markdown,.txt,.text,.rst"><button id="upload" type="button">Upload</button><p id="upload-status" class="small"></p><p id="selected-document" class="state-line">No file selected.</p></section>
      <section><h2>Documents</h2><div id="documents-list" class="document-list small">No uploaded documents.</div></section>
      <section><h2>Context</h2><pre id="context">{}</pre></section>
      <section><h2>Trace</h2><pre id="trace">No trace yet.</pre></section>
      <section><h2>SQL</h2><pre id="sql">No SQL yet.</pre></section>
      <section><h2>Tool Call</h2><pre id="tool">No tool call yet.</pre><h2>Tool Result</h2><pre id="tool-result">No tool result yet.</pre><button id="confirm" type="button" style="display:none">Confirm Action</button></section>
      <section><h2>Citations</h2><div id="citations" class="small">No citations yet.</div></section>
    </aside>
  </main>
  <script>
    const sessionId = localStorage.getItem("copilotSessionId") || crypto.randomUUID();
    localStorage.setItem("copilotSessionId", sessionId);
    let pendingActionId = null;
    const $ = (id) => document.getElementById(id);
    function addMessage(role, text) { const item = document.createElement("div"); item.className = "msg " + role; item.textContent = text; $("messages").appendChild(item); $("messages").scrollTop = $("messages").scrollHeight; }
    function normalizeContext(raw = {}) { return { current_project_id: raw.current_project_id || null, current_task_id: raw.current_task_id || null, current_document_id: raw.current_document_id || null, current_document_filename: raw.current_document_filename || null, current_note_id: raw.current_note_id || null, current_workflow_id: raw.current_workflow_id || null }; }
    function currentContext() { try { return normalizeContext(JSON.parse($("context").textContent)); } catch { return {}; } }
    function renderContext(raw = {}) { const c = normalizeContext(raw); $("selected-document").textContent = c.current_document_filename ? "Current file: " + c.current_document_filename : "No file selected."; $("context").textContent = JSON.stringify(c, null, 2); }
    async function readError(response) { try { const data = await response.json(); return data.detail || JSON.stringify(data); } catch { return await response.text(); } }
    async function loadDocuments() { try { const response = await fetch("/documents"); if (!response.ok) throw new Error(await readError(response)); renderDocuments((await response.json()).documents || []); } catch (error) { $("documents-list").textContent = "Could not load documents: " + error.message; } }
    function renderDocuments(documents) { const list = $("documents-list"); list.textContent = ""; if (!documents.length) { list.textContent = "No uploaded documents."; return; } const active = currentContext().current_document_id; for (const doc of documents) { const row = document.createElement("div"); row.className = "document-row"; row.innerHTML = '<div class="document-title"></div><div class="small"></div><div class="document-actions"></div>'; row.children[0].textContent = active === doc.document_id ? doc.filename + " (current)" : doc.filename; row.children[1].textContent = doc.chunk_count + " chunks"; const select = document.createElement("button"); select.className = "secondary"; select.textContent = "Select"; select.onclick = () => selectDocument(doc.document_id); const del = document.createElement("button"); del.className = "danger"; del.textContent = "Delete"; del.onclick = () => deleteDocument(doc.document_id, doc.filename); row.children[2].append(select, del); list.appendChild(row); } }
    async function selectDocument(id) { const form = new FormData(); form.append("session_id", sessionId); try { const response = await fetch("/documents/" + encodeURIComponent(id) + "/select", { method: "POST", body: form }); if (!response.ok) throw new Error(await readError(response)); const data = await response.json(); renderContext(data.context); addMessage("assistant", "Selected " + data.document.filename + " as the current file."); await loadDocuments(); } catch (error) { addMessage("assistant", "Select failed: " + error.message); } }
    async function deleteDocument(id, filename) { try { const response = await fetch("/documents/" + encodeURIComponent(id) + "?session_id=" + encodeURIComponent(sessionId), { method: "DELETE" }); if (!response.ok) throw new Error(await readError(response)); const data = await response.json(); if (data.context) renderContext(data.context); addMessage("assistant", data.deleted ? "Deleted " + filename + "." : filename + " was not found."); await loadDocuments(); } catch (error) { addMessage("assistant", "Delete failed: " + error.message); } }
    async function send(message, confirmActionId = null) { const body = { session_id: sessionId, message }; if (confirmActionId) body.confirm_action_id = confirmActionId; try { const response = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!response.ok) throw new Error(await readError(response)); const data = await response.json(); addMessage("assistant", data.response); renderContext(data.context); $("trace").textContent = data.trace_id ? "Trace: " + data.trace_id + "\\nOpen /debug to inspect it." : "No trace id returned."; $("sql").textContent = data.generated_sql || "No SQL for this turn."; $("tool").textContent = data.tool_call ? JSON.stringify(data.tool_call, null, 2) : "No tool call for this turn."; $("tool-result").textContent = data.tool_result ? JSON.stringify(data.tool_result, null, 2) : "No tool result for this turn."; pendingActionId = data.pending_action ? data.pending_action.action_id : null; $("confirm").style.display = pendingActionId ? "block" : "none"; $("citations").textContent = ""; if (data.citations.length) { for (const citation of data.citations) { const item = document.createElement("div"); item.className = "citation"; item.textContent = citation.filename + ": " + citation.quote; $("citations").appendChild(item); } } else { $("citations").textContent = "No citations for this turn."; } } catch (error) { addMessage("assistant", "Request failed: " + error.message); } }
    $("chat-form").addEventListener("submit", async (event) => { event.preventDefault(); const text = $("message").value.trim(); if (!text) return; $("message").value = ""; addMessage("user", text); await send(text); });
    $("confirm").addEventListener("click", async () => { if (!pendingActionId) return; addMessage("user", "Confirm action"); await send("Confirm action", pendingActionId); });
    $("upload").addEventListener("click", async () => { const file = $("file").files[0]; if (!file) return; const form = new FormData(); form.append("file", file); form.append("session_id", sessionId); $("upload").disabled = true; $("upload").textContent = "Uploading..."; try { const response = await fetch("/upload", { method: "POST", body: form }); if (!response.ok) throw new Error(await readError(response)); const data = await response.json(); $("upload-status").textContent = "Uploaded " + data.filename + " (" + data.chunk_count + " chunks)"; renderContext(data.context || { ...currentContext(), current_document_id: data.document_id, current_document_filename: data.filename }); addMessage("assistant", "Uploaded " + data.filename + " (" + data.chunk_count + " chunks). It is now the current file."); await loadDocuments(); } catch (error) { $("upload-status").textContent = "Upload failed: " + error.message; } finally { $("upload").disabled = false; $("upload").textContent = "Upload"; } });
    addMessage("assistant", "Ask about uploaded docs, query tasks, or request task actions. Writes require confirmation.");
    renderContext({});
    loadDocuments();
  </script>
</body>
</html>`;

const DEBUG_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Project Copilot Traces</title>
  <style>
    body { margin:0; font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#f7f8fa; color:#1f2933; }
    main { display:grid; grid-template-columns:360px 1fr; min-height:100vh; }
    aside, section { padding:16px; }
    aside { border-right:1px solid #d7dde5; background:#fff; overflow:auto; }
    button { width:100%; text-align:left; border:1px solid #d7dde5; background:#fff; border-radius:6px; padding:10px; margin:0 0 8px; cursor:pointer; }
    pre { background:#0f172a; color:#e2e8f0; border-radius:6px; padding:12px; overflow:auto; }
    a { color:#2563eb; text-decoration:none; font-weight:650; }
    .small { color:#64748b; font-size:13px; }
    @media (max-width:860px) { main { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid #d7dde5; } }
  </style>
</head>
<body>
  <main>
    <aside><a href="/">Back to app</a><h1>Traces</h1><div id="traces" class="small">Loading...</div></aside>
    <section><h1>Detail</h1><pre id="detail">Select a trace.</pre></section>
  </main>
  <script>
    const list = document.getElementById("traces");
    const detail = document.getElementById("detail");
    async function load() {
      const response = await fetch("/debug/traces");
      const data = await response.json();
      list.textContent = "";
      if (!data.traces.length) { list.textContent = "No traces yet."; return; }
      for (const trace of data.traces) {
        const button = document.createElement("button");
        button.textContent = trace.route + " - " + trace.user_message;
        button.onclick = async () => {
          const traceResponse = await fetch("/debug/traces/" + encodeURIComponent(trace.trace_id));
          detail.textContent = JSON.stringify(await traceResponse.json(), null, 2);
        };
        list.appendChild(button);
      }
    }
    load();
  </script>
</body>
</html>`;
