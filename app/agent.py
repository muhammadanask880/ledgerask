"""
The orchestration loop: the user's question goes to an OpenAI model along
with the TOOL_SPECS menu from app.tools. The model decides which tool(s) to
call, we execute them for real against the read-only business database, and
feed the results back until the model is ready to give a final answer.

`ask_stream` is a generator that yields events as they actually happen -
"a tool was just called", "here's its result", "here's the next chunk of
the final answer" - so the web UI can render the tool-call proof live and
type out the answer, instead of waiting in silence for the whole thing.
`ask` is a thin convenience wrapper that drains the generator and returns
the final {"answer", "trace"} dict, used by scripts/tests.
"""
import json
from typing import Any, Iterator, Optional

from openai import OpenAI

from app.config import settings
from app.tools import TOOL_SPECS, call_tool

SYSTEM_PROMPT = (
    "You are LedgerAsk, an assistant that answers questions about a company's "
    "sales data by calling the read-only tools you've been given. Always use a "
    "tool to look up real numbers before answering - never guess or make up "
    "data. Keep answers concise, use a markdown table for lists of numbers, and "
    "mention the time window the numbers cover. Tool results label months as "
    "explicit 'YYYY-MM' strings - always read the actual month labels from the "
    "tool output and use those exact months in your answer (e.g. a row labeled "
    "'2026-10' is October 2026). Never assume what today's date is or guess "
    "which month is 'this month' - the monthly tools already exclude any "
    "still-in-progress month, so the most recent month returned is the most "
    "recent complete one. If the user asks you to add, change, delete, or "
    "otherwise modify any data (e.g. 'delete last year's orders'), do not call "
    "any tool to explore the request first - immediately refuse in your reply "
    "and explain that you only have read-only access, so that action isn't "
    "possible. Only call tools for questions that are actually asking to look "
    "something up. If the question is a greeting or small talk (e.g. 'hello', "
    "'what can you do'), answer briefly and naturally without calling a tool, "
    "and give 2-3 concrete example questions the user could ask instead."
)

MAX_TOOL_ROUNDS = 5
# Word-group size used to simulate a typing effect for the final answer.
STREAM_CHUNK_WORDS = 3


def _client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def _chunk_text(text: str, chunk_size: int = STREAM_CHUNK_WORDS) -> Iterator[str]:
    words = text.split(" ")
    buf: list[str] = []
    for word in words:
        buf.append(word)
        if len(buf) >= chunk_size:
            yield " ".join(buf) + " "
            buf = []
    if buf:
        yield " ".join(buf)


def ask_stream(
    question: str,
    *,
    user_email: str,
    history: Optional[list[dict[str, str]]] = None,
) -> Iterator[dict[str, Any]]:
    """Run the tool-calling loop, yielding events as they happen:

    {"type": "tool_start", "tool": str, "arguments": dict}
    {"type": "tool_result", "step": {...}}               # the proof-panel entry
    {"type": "answer_chunk", "text": str}                 # a piece of the final answer
    {"type": "final", "answer": str}                      # the complete final answer
    """
    client = _client()
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-10:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=TOOL_SPECS,
            tool_choice="auto",
        )
        message = response.choices[0].message

        if not message.tool_calls:
            text = message.content or ""
            for chunk in _chunk_text(text):
                yield {"type": "answer_chunk", "text": chunk}
            yield {"type": "final", "answer": text}
            return

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            }
        )

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}

            yield {"type": "tool_start", "tool": name, "arguments": arguments}

            try:
                tool_output = call_tool(name, arguments, user_email=user_email, question=question)
                status = "ok"
                error = None
            except Exception as exc:  # noqa: BLE001 - reported to both the model and the trace
                tool_output = {"error": str(exc)}
                status = "error"
                error = str(exc)

            step = {
                "tool": name,
                "arguments": arguments,
                "sql": tool_output.get("sql"),
                "columns": tool_output.get("columns"),
                "rows": tool_output.get("rows"),
                "truncated": tool_output.get("truncated", False),
                "status": status,
                "error": error,
            }
            yield {"type": "tool_result", "step": step}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_output, default=str)[:8000],
                }
            )

    fallback = "I wasn't able to finish answering that within the allowed number of tool calls."
    for chunk in _chunk_text(fallback):
        yield {"type": "answer_chunk", "text": chunk}
    yield {"type": "final", "answer": fallback}


def ask(question: str, *, user_email: str, history: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
    """Convenience wrapper: drains ask_stream() and returns {"answer", "trace"}."""
    trace: list[dict[str, Any]] = []
    answer = ""
    for event in ask_stream(question, user_email=user_email, history=history):
        if event["type"] == "tool_result":
            trace.append(event["step"])
        elif event["type"] == "final":
            answer = event["answer"]
    return {"answer": answer, "trace": trace}
