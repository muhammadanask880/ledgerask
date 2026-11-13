// Chat UI: streams questions to /api/chat as newline-delimited JSON events
// (tool_start, tool_result, answer_chunk, final, error) and renders the
// answer live - tool calls appear as they happen, the answer types itself
// out - plus the step-by-step "proof" panel that produced it.

const messagesEl = document.getElementById("messages");
const emptyStateEl = document.getElementById("empty-state");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const sendBtn = document.getElementById("chat-send");

let busy = false;
let msgCounter = 0;

const ASSISTANT_AVATAR_SVG = `
<span class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-400 to-teal-400">
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
    <rect x="4" y="13" width="4" height="7" rx="1" fill="white" fill-opacity="0.85"/>
    <rect x="10" y="9" width="4" height="11" rx="1" fill="white" fill-opacity="0.95"/>
    <rect x="16" y="4" width="4" height="16" rx="1" fill="white"/>
  </svg>
</span>`;

const USER_AVATAR_SVG = `
<span class="avatar-user">
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
    <circle cx="12" cy="8" r="4" stroke="white" stroke-width="2"/>
    <path d="M4 20c0-4 3.5-7 8-7s8 3 8 7" stroke="white" stroke-width="2" stroke-linecap="round"/>
  </svg>
</span>`;

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
}

function renderMarkdown(text) {
    const raw = marked.parse(text || "", { breaks: true });
    return DOMPurify.sanitize(raw);
}

// Lightweight SQL highlighter - good enough for display, not a real parser.
const SQL_TOKEN_RE =
    /('(?:[^'\\]|\\.)*')|\b(SELECT|FROM|WHERE|JOIN|ON|GROUP BY|ORDER BY|LIMIT|AS|AND|OR|DESC|ASC|WITH|DISTINCT|INNER|LEFT|NOT|NULL|IS|IN|LIKE|CASE|WHEN|THEN|ELSE|END)\b|\b(COUNT|SUM|ROUND|STRFTIME|DATE|MAX|MIN|AVG|COALESCE)(?=\s*\()|\b(\d+(?:\.\d+)?)\b/gi;

function highlightSql(sql) {
    const escaped = escapeHtml(sql.trim());
    return escaped.replace(SQL_TOKEN_RE, (match, str, kw, fn, num) => {
        if (str) return `<span class="sql-str">${str}</span>`;
        if (kw) return `<span class="sql-kw">${kw}</span>`;
        if (fn) return `<span class="sql-fn">${fn}</span>`;
        if (num) return `<span class="sql-num">${num}</span>`;
        return match;
    });
}

function renderRowsTable(columns, rows) {
    if (!columns || !rows || rows.length === 0) {
        return '<p class="text-xs text-slate-500 italic px-1">No rows returned.</p>';
    }
    const head = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
    const body = rows
        .slice(0, 15)
        .map((row) => {
            const cells = columns
                .map((c) => {
                    const val = row[c];
                    const isNum = typeof val === "number";
                    return `<td class="${isNum ? "num" : ""}">${escapeHtml(val)}</td>`;
                })
                .join("");
            return `<tr>${cells}</tr>`;
        })
        .join("");
    const more = rows.length > 15 ? `<p class="text-xs text-slate-500 mt-1.5 px-1">+ ${rows.length - 15} more row(s)</p>` : "";
    return `<div class="overflow-x-auto rounded-lg border border-white/5"><table class="result-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
}

function toolCardHtml(step) {
    const isOk = step.status === "ok";
    const statusHtml = isOk
        ? `<span class="status-pill ok"><svg width="9" height="9" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>ok</span>`
        : `<span class="status-pill error"><svg width="9" height="9" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5"/><path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>error</span>`;
    const sqlBlock = step.sql
        ? `<pre class="sql-block mt-2.5 p-3"><code>${highlightSql(step.sql)}</code></pre>`
        : "";
    const errorBlock = step.error
        ? `<p class="mt-2 text-xs text-red-400 px-1">${escapeHtml(step.error)}</p>`
        : "";
    const rowsBlock = isOk ? `<div class="mt-2.5">${renderRowsTable(step.columns, step.rows)}</div>` : "";
    const argsText = JSON.stringify(step.arguments || {});

    return `
        <details class="tool-card px-3 py-2.5">
            <summary class="cursor-pointer flex items-center gap-2 text-xs select-none">
                <svg class="chev text-slate-500" width="10" height="10" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
                ${statusHtml}
                <span class="font-medium text-slate-300">${escapeHtml(step.tool)}</span>
                <span class="text-slate-600 truncate">${escapeHtml(argsText)}</span>
            </summary>
            ${sqlBlock}
            ${errorBlock}
            ${rowsBlock}
        </details>`;
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendUserMessage(text) {
    emptyStateEl.classList.add("hidden");
    const wrapper = document.createElement("div");
    wrapper.className = "flex gap-3 justify-end msg-enter";
    wrapper.innerHTML = `
        <div class="max-w-[82%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-brand-500 to-brand-600 px-4 py-2.5 text-sm text-white shadow-glow">${escapeHtml(text)}</div>
        ${USER_AVATAR_SVG}`;
    messagesEl.appendChild(wrapper);
    scrollToBottom();
}

/** Creates an empty assistant message shell and returns handles to update it live. */
function appendAssistantShell() {
    emptyStateEl.classList.add("hidden");
    const id = `msg-${++msgCounter}`;
    const wrapper = document.createElement("div");
    wrapper.className = "flex gap-3 justify-start msg-enter";
    wrapper.innerHTML = `
        ${ASSISTANT_AVATAR_SVG}
        <div class="max-w-[88%] flex-1 rounded-2xl rounded-tl-sm border border-white/10 bg-white/[0.03] px-4 py-3 text-sm" id="${id}">
            <div class="status-line flex items-center gap-2 text-slate-400">
                <span class="typing-dots"><span></span><span></span><span></span></span>
            </div>
            <p class="proof-label hidden text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2 mt-2">Proof — tools called</p>
            <div class="proof-container space-y-2"></div>
            <div class="answer-text prose prose-sm prose-invert max-w-none mt-0"></div>
        </div>`;
    messagesEl.appendChild(wrapper);
    scrollToBottom();

    const bubble = wrapper.querySelector(`#${id}`);
    return {
        statusLine: bubble.querySelector(".status-line"),
        proofLabel: bubble.querySelector(".proof-label"),
        proofContainer: bubble.querySelector(".proof-container"),
        answerText: bubble.querySelector(".answer-text"),
    };
}

function setStatus(handles, label) {
    handles.statusLine.innerHTML = `
        <span class="typing-dots"><span></span><span></span><span></span></span>
        <span class="text-xs">${escapeHtml(label)}</span>`;
}

function hideStatus(handles) {
    handles.statusLine.classList.add("hidden");
}

function addToolCardLive(handles, step) {
    handles.proofLabel.classList.remove("hidden");
    const card = document.createElement("div");
    card.innerHTML = toolCardHtml(step);
    handles.proofContainer.appendChild(card.firstElementChild);
    scrollToBottom();
}

function updateAnswerText(handles, text) {
    handles.answerText.innerHTML = renderMarkdown(text);
    scrollToBottom();
}

function showErrorInBubble(handles, message) {
    hideStatus(handles);
    handles.answerText.innerHTML = `<p class="text-red-300">Sorry, something went wrong: ${escapeHtml(message)}</p>`;
}

/** The first message in a new chat auto-names it - reflect that live, without a reload. */
function applyConversationTitle(title) {
    document.title = `${title} — LedgerAsk`;
    const headerEl = document.getElementById("chat-title");
    if (headerEl) headerEl.textContent = title;
    const sidebarEl = document.getElementById("sidebar-active-link");
    if (sidebarEl) sidebarEl.textContent = title;
}

/** Streams NDJSON events from /api/chat, calling onEvent for each parsed line. */
async function streamChat(question, onEvent) {
    const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, conversation_id: window.LEDGERASK_CONVERSATION_ID }),
    });

    if (res.status === 401) {
        window.location.href = "/login";
        return;
    }
    if (!res.ok || !res.body) {
        let detail = res.statusText;
        try {
            const err = await res.json();
            detail = err.detail || detail;
        } catch (e) {
            /* response wasn't JSON - keep statusText */
        }
        onEvent({ type: "error", message: detail });
        return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
            if (!line.trim()) continue;
            try {
                onEvent(JSON.parse(line));
            } catch (e) {
                /* skip a malformed line rather than breaking the whole stream */
            }
        }
    }
    if (buffer.trim()) {
        try {
            onEvent(JSON.parse(buffer));
        } catch (e) {
            /* ignore trailing partial garbage */
        }
    }
}

function setBusy(isBusy) {
    busy = isBusy;
    inputEl.disabled = isBusy;
    sendBtn.disabled = isBusy;
    document.querySelectorAll(".suggestion").forEach((b) => (b.disabled = isBusy));
    sendBtn.querySelector(".send-label").textContent = isBusy ? "···" : "Ask";
}

async function sendMessage(text) {
    if (busy) return; // a request is already in flight - never let a second one race it
    setBusy(true);

    appendUserMessage(text);
    const handles = appendAssistantShell();

    let answerText = "";
    let gotFinal = false;

    try {
        await streamChat(text, (event) => {
            if (event.type === "title") {
                applyConversationTitle(event.title);
            } else if (event.type === "tool_start") {
                setStatus(handles, `Calling ${event.tool}...`);
            } else if (event.type === "tool_result") {
                addToolCardLive(handles, event.step);
                setStatus(handles, "Working out the answer...");
            } else if (event.type === "answer_chunk") {
                hideStatus(handles);
                answerText += event.text;
                updateAnswerText(handles, answerText);
            } else if (event.type === "final") {
                gotFinal = true;
                hideStatus(handles);
                updateAnswerText(handles, event.answer);
            } else if (event.type === "error") {
                gotFinal = true;
                showErrorInBubble(handles, event.message);
            }
        });
    } catch (e) {
        if (!gotFinal) showErrorInBubble(handles, "Couldn't reach the server. Please try again.");
    } finally {
        hideStatus(handles);
        setBusy(false);
        inputEl.focus();
    }
}

formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = inputEl.value.trim();
    if (!text || busy) return;
    inputEl.value = "";
    sendMessage(text);
});

document.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
        if (busy) return;
        sendMessage(btn.textContent.trim());
    });
});

// Render any existing history (already complete - no streaming needed).
function renderHistoryMessage(m) {
    if (m.role === "user") {
        appendUserMessage(m.content);
        return;
    }
    const handles = appendAssistantShell();
    hideStatus(handles);
    (m.trace || []).forEach((step) => addToolCardLive(handles, step));
    updateAnswerText(handles, m.content);
}

const history = JSON.parse(document.getElementById("history-data").textContent || "[]");
if (history.length === 0) {
    emptyStateEl.classList.remove("hidden");
} else {
    history.forEach(renderHistoryMessage);
}
