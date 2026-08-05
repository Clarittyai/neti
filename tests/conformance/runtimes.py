"""Each framework's *own* execution path, driven with no model at all.

`tests/e2e/test_seam_equivalence.py` proves the adapters honour each framework's contract. It does
that by calling the integration point directly — `gated.invoke(...)`, `plugin.before_tool_callback
(...)` — which is the right way to compare twelve seams against each other, and it leaves one thing
unproven: that when the framework runs the tool *its own way*, the gate is actually in the path.

That gap is not theoretical. This repository has already shipped a defect of exactly that shape — a
blocked call raising `TypeError` inside LangGraph's `ToolNode`, killing the whole graph — which was
invisible from the adapter and only appeared when the framework executed the tool itself.

So every row here builds a real agent or a real executor out of the framework's own public API and
runs it. What none of them build is a model. **The gate is at the execution seam: the model chooses
what to call, and the gate decides whether it runs.** Every row proves that by never having a model
at all — a scripted response, a `TestModel`, or in LangGraph's case a hand-written `AIMessage`. No
key, no network, no provider, no cost, and the same answer every time, which is why this runs in CI
on every push rather than being a demonstration somebody gave once.

**Two depths, never conflated.** `AGENT_LOOP` means the framework's full agent loop ran and reached
the tool through its own planner. `EXECUTOR` means its tool-execution component ran but the loop
above it did not. The second is weaker and says so, in the table and on the card: a matrix that
prints both as "tested" would be exactly the kind of claim this project exists to distrust.

What is asserted is the same triple the seam table asserts, for the same reason: the tool function
**never ran**, and the sentence handed back is byte-for-byte the sentence `Preflight` produced. The
sentence is the product's real output — it is what the model reads and what makes it retry with a
narrower target — so a framework that quietly rephrases it has changed the thing the agent acts on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from neti.preflight import Preflight

AGENT_LOOP = "agent_loop"
EXECUTOR = "executor"

TOOL = "Glob"
"""The call every runtime is asked to make. `fs.paths` is used rather than Entra because it needs no
credentials and no synthetic tenant: the fixture tree is on disk, the count is 30, and the ceiling
in the `fs` world is 10, so a framework that lets it through is unmistakable."""


@dataclass(frozen=True)
class Driven:
    """What came back out of one framework."""

    ran: bool
    """Whether the tool's own function body executed. The only assertion that cannot be faked by a
    framework returning a plausible-looking error."""

    sentence: str


@dataclass(frozen=True)
class Runtime:
    name: str
    needs: tuple[str, ...]
    """Imports this row cannot run without. Absent means skipped, and skipped is recorded."""

    depth: str
    what: str
    drive: Callable[[Preflight, dict[str, Any]], Driven]


def _sentence_in(texts: list[str]) -> str:
    """Pull the gate's sentence out of whatever the agent was shown.

    Three runtimes hand the denial back inside a transcript rather than as a return value, so the
    only place that proves it *travelled* is the text the model saw on its next turn. Asserting on
    a framework's final output instead would be asserting on the scripted reply.
    """
    import re

    for text in reversed(texts):
        # Stops at a quote, a backslash or a `<`: Semantic Kernel serialises its chat history as
        # XML, so an unbounded match swallows `</function_result></message>` and the row fails on
        # the transcript's markup rather than on the product's words.
        found = re.search(r"Preflight blocked[^'\"<\\]*", text)
        if found:
            return found.group(0).strip()
    return ""


# --------------------------------------------------------------------------- the drivers


def _langgraph(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """A compiled `StateGraph` with a `ToolNode`, driven by a hand-written `AIMessage`.

    The purest statement of the thesis in the whole suite: there is no model here, not even a fake
    one. A tool call is just a message, and the graph executes it the way it would execute one a
    model produced. LangGraph 1.x refuses to invoke a bare node outside a graph runtime, which is
    convenient — compiling the graph is closer to how anybody actually runs this.
    """
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.graph import END, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    def Glob(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    tool = StructuredTool.from_function(func=Glob, name=TOOL, description="Match files.")
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(gate_tools(preflight, [tool])))
    builder.set_entry_point("tools")
    builder.add_edge("tools", END)

    call = {"name": TOOL, "args": dict(args), "id": "call_1", "type": "tool_call"}
    out = builder.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call])]})
    return Driven(ran=bool(ran), sentence=str(out["messages"][-1].content))


def _langchain(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """`create_agent` — the agent LangChain users actually build — over a fake chat model.

    Distinct from the LangGraph row and worth keeping: that one hands the graph a tool call
    directly, which proves the executor. This one goes through the **model interface**, so the call
    arrives the way a real one does — `bind_tools`, an `AIMessage` carrying `tool_calls`, the
    prebuilt ReAct loop deciding what to do with the result — and it proves the denial is something
    the loop can carry and continue from rather than an exception that unwinds it.

    Deliberately `langchain.agents.create_agent` rather than `AgentExecutor` or LangGraph's
    `create_react_agent`: 1.x moved the first out of the package and deprecated the second in
    favour of this one. Pinning a conformance claim to a legacy constructor would make the row
    quietly untrue on a current install, which is the failure mode this whole directory is about.
    The fake model is a `BaseChatModel` subclass, which is the documented extension point.
    """
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import StructuredTool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    def Glob(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    class Scripted(BaseChatModel):
        """One tool call, then stop. No provider, no key, no network."""

        turns: int = 0

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def _generate(self, messages: list[Any], *a: Any, **kw: Any) -> ChatResult:
            self.turns += 1
            if self.turns == 1:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {"name": TOOL, "args": dict(args), "id": "call_1", "type": "tool_call"}
                    ],
                )
            else:
                message = AIMessage(content="stopped")
            return ChatResult(generations=[ChatGeneration(message=message)])

        def bind_tools(self, tools: Any, **kw: Any) -> Any:
            # The prebuilt agent binds before running. Nothing to do: what this model emits is
            # scripted, and pretending otherwise would only hide which tools it was offered.
            return self

    tool = StructuredTool.from_function(func=Glob, name=TOOL, description="Match files.")
    agent = create_agent(Scripted(), gate_tools(preflight, [tool]))
    out = agent.invoke({"messages": [("user", "go")]})

    sentence = ""
    for message in out["messages"]:
        if type(message).__name__ == "ToolMessage":
            sentence = str(message.content)
    return Driven(ran=bool(ran), sentence=sentence)


def _openai_agents(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """`Runner.run` over a `Model` implementation that returns one function call.

    The SDK's own loop, its own guardrail machinery, its own tool executor. The fake model is
    thirty lines and implements the documented `Model` interface, so nothing here reaches a
    provider — `OPENAI_API_KEY` is not set and the run does not want one.
    """
    import asyncio

    from agents import Agent, ModelResponse, Runner, Usage, function_tool
    from agents.models.interface import Model
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )

    from neti.adapters.openai_agents import neti_guardrail

    ran: list[str] = []
    gate = neti_guardrail(preflight)

    @function_tool(tool_input_guardrails=[gate], name_override=TOOL)
    def glob_tool(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    class Scripted(Model):
        def __init__(self) -> None:
            self.turns = 0

        async def get_response(self, *a: Any, **kw: Any) -> Any:
            self.turns += 1
            if self.turns == 1:
                output: list[Any] = [
                    ResponseFunctionToolCall(
                        arguments=json.dumps(args),
                        call_id="c1",
                        name=TOOL,
                        type="function_call",
                        id="fc1",
                    )
                ]
            else:
                # The second turn is where the agent reads the denial and stops. Echoing it back
                # is what lets this row assert on the sentence the *model* saw.
                output = [
                    ResponseOutputMessage(
                        id="m1",
                        role="assistant",
                        status="completed",
                        type="message",
                        content=[
                            ResponseOutputText(annotations=[], text="stopped", type="output_text")
                        ],
                    )
                ]
            return ModelResponse(output=output, usage=Usage(), response_id=None)

        def stream_response(self, *a: Any, **kw: Any) -> Any:
            raise NotImplementedError

    agent = Agent(name="conformance", tools=[glob_tool], model=Scripted())
    result = asyncio.run(Runner.run(agent, "go"))

    sentence = ""
    for item in result.new_items:
        if type(item).__name__ == "ToolCallOutputItem":
            sentence = str(item.output)
    return Driven(ran=bool(ran), sentence=sentence)


def _pydantic_ai(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """`FunctionModel`, which Pydantic AI ships so a loop can be run without a provider."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from neti.adapters.pydantic_ai import neti_hooks

    ran: list[str] = []
    seen: list[str] = []

    def model(messages: list[Any], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(TOOL, dict(args))])
        for message in messages[-1].parts:
            content = getattr(message, "content", "")
            if isinstance(content, str) and content:
                seen.append(content)
        return ModelResponse(parts=[TextPart("stopped")])

    agent = Agent(FunctionModel(model), capabilities=[neti_hooks(preflight)])

    @agent.tool_plain(name=TOOL)
    def glob_tool(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    agent.run_sync("go")
    return Driven(ran=bool(ran), sentence=seen[-1] if seen else "")


def _google_adk(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """A real `App` with the neti plugin, run by `InMemoryRunner` over a scripted `BaseLlm`.

    ADK is the one runtime here where the gate is a *plugin* rather than something wrapped around
    the tool, so this row is checking a different thing from the others: that a plugin registered
    on the app is consulted for a tool the agent owns, through the runner's own event loop.
    """
    import asyncio
    from collections.abc import AsyncGenerator

    from google.adk.agents import LlmAgent
    from google.adk.apps import App
    from google.adk.models import BaseLlm, LlmRequest, LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from neti.adapters.google_adk import neti_plugin

    ran: list[str] = []

    def Glob(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    class Scripted(BaseLlm):
        model: str = "scripted"
        turns: int = 0

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            self.turns += 1
            if self.turns == 1:
                part = types.Part.from_function_call(name=TOOL, args=dict(args))
            else:
                part = types.Part.from_text(text="stopped")
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    app = App(
        name="conformance",
        root_agent=LlmAgent(name="ops", model=Scripted(), tools=[Glob]),
        plugins=[neti_plugin(preflight)],
    )
    runner = InMemoryRunner(app=app)

    async def go() -> str:
        session = await runner.session_service.create_session(app_name="conformance", user_id="u")
        sentence = ""
        async for event in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")]),
        ):
            for part in event.content.parts if event.content else []:
                if part.function_response:
                    response = part.function_response.response or {}
                    sentence = str(response.get("error", ""))
        return sentence

    return Driven(ran=bool(ran), sentence=asyncio.run(go()))


def _autogen(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """`AssistantAgent.run` over a `ChatCompletionClient` that returns one `FunctionCall`.

    AutoGen is the runtime with no before-tool hook at all, so the gate lives in a wrapped
    `Workbench` — which means this row is the one that proves the agent goes *through* the
    workbench rather than around it. `autogen_ext`'s `ReplayChatCompletionClient` would be the
    obvious fake, and it is deliberately not used: it is a separate distribution this project does
    not otherwise need, and the documented `ChatCompletionClient` interface is thirty lines.
    """
    import asyncio

    from autogen_agentchat.agents import AssistantAgent
    from autogen_core import FunctionCall
    from autogen_core.models import ChatCompletionClient, CreateResult, ModelInfo, RequestUsage
    from autogen_core.tools import FunctionTool, StaticWorkbench

    from neti.adapters.autogen_tools import gate_workbench

    ran: list[str] = []

    def Glob(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    class Scripted(ChatCompletionClient):
        def __init__(self) -> None:
            self.turns = 0

        async def create(self, messages: Any, *, tools: Any = (), **kw: Any) -> CreateResult:
            self.turns += 1
            content: Any = "stopped"
            if self.turns == 1:
                content = [FunctionCall(id="c1", name=TOOL, arguments=json.dumps(args))]
            return CreateResult(
                finish_reason="stop",
                content=content,
                usage=RequestUsage(prompt_tokens=0, completion_tokens=0),
                cached=False,
            )

        def create_stream(self, *a: Any, **kw: Any) -> Any:
            raise NotImplementedError

        async def close(self) -> None:
            return None

        def actual_usage(self) -> RequestUsage:
            return RequestUsage(prompt_tokens=0, completion_tokens=0)

        def total_usage(self) -> RequestUsage:
            return RequestUsage(prompt_tokens=0, completion_tokens=0)

        def count_tokens(self, *a: Any, **kw: Any) -> int:
            return 0

        def remaining_tokens(self, *a: Any, **kw: Any) -> int:
            return 1000

        @property
        def capabilities(self) -> ModelInfo:
            return self.model_info

        @property
        def model_info(self) -> ModelInfo:
            return ModelInfo(
                vision=False,
                function_calling=True,
                json_output=False,
                family="unknown",
                structured_output=False,
            )

    bench = StaticWorkbench([FunctionTool(Glob, description="Match files.", name=TOOL)])
    agent = AssistantAgent(
        "ops", model_client=Scripted(), workbench=gate_workbench(preflight, bench)
    )

    async def go() -> str:
        result = await agent.run(task="go")
        sentence = ""
        for message in result.messages:
            for item in message.content if isinstance(message.content, list) else []:
                if hasattr(item, "content"):
                    sentence = str(item.content)
        return sentence

    return Driven(ran=bool(ran), sentence=asyncio.run(go()))


def _anthropic(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """`client.beta.messages.tool_runner`, against a mock HTTP transport.

    The sentence is read **off the wire** — out of the body of the second request the SDK sends —
    rather than out of a return value. That is the strongest form this assertion takes anywhere in
    the suite: it is not what the adapter returned, it is what was transmitted to the model.

    The precedent is `R6 declines to answer against a mock transport instead of flaking`. An API key
    is set to a string that is not a key, because the SDK requires one to construct and the
    transport never reaches a network.
    """
    import httpx
    from anthropic import Anthropic
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    ran: list[str] = []

    @beta_tool
    def Glob(pattern: str) -> str:
        """Match files."""
        ran.append(pattern)
        return "ran"

    turns = {"n": 0}
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns["n"] += 1
        sent.append(json.loads(request.content))
        if turns["n"] == 1:
            body = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "scripted",
                "content": [{"type": "tool_use", "id": "tu1", "name": TOOL, "input": args}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:
            body = {
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "scripted",
                "content": [{"type": "text", "text": "stopped"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return httpx.Response(200, json=body)

    client = Anthropic(
        api_key="not-a-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    runner = client.beta.messages.tool_runner(
        model="scripted",
        max_tokens=256,
        tools=gate_tools(preflight, [Glob]),
        messages=[{"role": "user", "content": "go"}],
    )
    for _ in runner:
        pass

    sentence = ""
    for body in sent:
        for message in body.get("messages", []):
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    sentence = str(block.get("content"))
    return Driven(ran=bool(ran), sentence=sentence)


def _crewai(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """A real `Crew.kickoff()` over a `BaseLLM` that answers in CrewAI's own ReAct format.

    This is the row that found the defect this directory was written to find. Driving CrewAI's
    hook pair by hand — the way `test_seam_equivalence.py` did — reported a perfect denial, and
    running `kickoff()` showed the agent being handed `Tool execution blocked by hook. Tool: Glob`
    with no magnitude in it at all, because CrewAI returns the moment a before-hook says `False`
    and the after-hook that was supposed to restore the sentence never runs. The gate is a wrapped
    tool now, and this row is what holds it there.

    `BaseLLM` rather than `LLM`: the latter resolves a native provider in `__new__` and demands
    `OPENAI_API_KEY` before any method of ours could run. `BaseLLM` is the documented extension
    point for a custom model and reaches nothing.
    """
    from crewai import Agent, Crew, Task
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import BaseTool

    from neti.adapters.crewai_hooks import gate_tools

    ran: list[str] = []
    seen: list[str] = []

    class GlobTool(BaseTool):
        name: str = TOOL
        description: str = "Match files."

        def _run(self, pattern: str) -> str:
            ran.append(pattern)
            return "ran"

    class Scripted(BaseLLM):
        turns: int = 0

        def call(
            self,
            messages: Any,
            tools: Any = None,
            callbacks: Any = None,
            available_functions: Any = None,
            from_task: Any = None,
            from_agent: Any = None,
            response_model: Any = None,
        ) -> str:
            self.turns += 1
            seen.append(str(messages))
            if self.turns == 1:
                return (
                    "Thought: I should look.\nAction: "
                    + TOOL
                    + "\nAction Input: "
                    + json.dumps(args)
                )
            return "Thought: done\nFinal Answer: stopped"

        def supports_function_calling(self) -> bool:
            return False

    agent = Agent(
        role="ops",
        goal="measure",
        backstory="tests",
        llm=Scripted(model="scripted"),
        tools=gate_tools(preflight, [GlobTool()]),
        verbose=False,
    )
    task = Task(description="go", expected_output="text", agent=agent)
    Crew(agents=[agent], tasks=[task], verbose=False).kickoff()

    # Read the sentence out of what the agent was *shown* on its next turn. Anywhere else and this
    # row would be asserting on something the model never saw, which is the exact mistake the
    # hand-driven version made.
    sentence = ""
    for body in seen:
        for line in body.split("Observation:")[1:]:
            sentence = line.strip().strip("'\"}]").strip()
    return Driven(ran=bool(ran), sentence=sentence)


def _llamaindex(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """A real `FunctionAgent`, over a `FunctionCallingLLM` that emits one tool call.

    The sentence is read out of the chat history the agent shows the model on its next turn, which
    is the only place that proves the denial travelled — an assertion on the agent's final output
    would be asserting on the scripted second turn.
    """
    import asyncio
    from collections.abc import AsyncGenerator

    from llama_index.core.agent.workflow import FunctionAgent
    from llama_index.core.base.llms.types import ChatMessage, ChatResponse, LLMMetadata, MessageRole
    from llama_index.core.llms.function_calling import FunctionCallingLLM
    from llama_index.core.tools import FunctionTool, ToolSelection

    from neti.adapters.llamaindex_tools import gate_tools

    ran: list[str] = []
    seen: list[str] = []

    def Glob(pattern: str) -> str:
        ran.append(pattern)
        return "ran"

    class Scripted(FunctionCallingLLM):
        turns: int = 0

        @property
        def metadata(self) -> LLMMetadata:
            return LLMMetadata(model_name="scripted", is_function_calling_model=True)

        def _prepare_chat_with_tools(self, tools: Any, **kw: Any) -> dict[str, Any]:
            return {"messages": kw.get("chat_history") or []}

        def get_tool_calls_from_response(self, response: Any, **kw: Any) -> list[Any]:
            return list(getattr(response, "_selections", []))

        async def achat_with_tools(
            self, tools: Any, user_msg: Any = None, chat_history: Any = None, **kw: Any
        ) -> Any:
            self.turns += 1
            seen.append(str(chat_history))
            content = "" if self.turns == 1 else "stopped"
            response = ChatResponse(
                message=ChatMessage(role=MessageRole.ASSISTANT, content=content)
            )
            response._selections = (
                [ToolSelection(tool_id="c1", tool_name=TOOL, tool_kwargs=dict(args))]
                if self.turns == 1
                else []
            )
            return response

        async def astream_chat_with_tools(self, tools: Any, **kw: Any) -> Any:
            response = await self.achat_with_tools(tools, **kw)

            async def one() -> AsyncGenerator[Any, None]:
                yield response

            return one()

        def chat(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        def complete(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        def stream_chat(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        def stream_complete(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        async def achat(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        async def acomplete(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        async def astream_chat(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        async def astream_complete(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

    tool = FunctionTool.from_defaults(fn=Glob, name=TOOL, description="Match files.")
    agent = FunctionAgent(tools=gate_tools(preflight, [tool]), llm=Scripted())

    async def go() -> None:
        await agent.run("go")

    asyncio.run(go())
    return Driven(ran=bool(ran), sentence=_sentence_in(seen))


def _smolagents(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """A real `ToolCallingAgent`, over a scripted `Model`.

    This row caught the adapter returning something the framework would not accept: the first
    version wrapped the tool in a plain delegating object, which passed every direct test and made
    `ToolCallingAgent` raise *All elements must be instance of BaseTool*. The observation the agent
    records is the sentence, so that is what is read here.
    """
    from smolagents import Tool as SmolTool
    from smolagents import ToolCallingAgent
    from smolagents.models import (
        ChatMessage,
        ChatMessageToolCall,
        ChatMessageToolCallFunction,
        Model,
    )

    from neti.adapters.smolagents_tools import gate_tools

    ran: list[str] = []

    class GlobTool(SmolTool):
        name = TOOL
        description = "Match files."
        # smolagents reads these as class attributes; ClassVar is the annotation that says so.
        inputs: ClassVar[dict[str, Any]] = {"pattern": {"type": "string", "description": "a glob"}}
        output_type = "string"

        def forward(self, pattern: str) -> str:
            ran.append(pattern)
            return "ran"

    def call(name: str, arguments: dict[str, Any], identifier: str) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                ChatMessageToolCall(
                    id=identifier,
                    type="function",
                    function=ChatMessageToolCallFunction(name=name, arguments=arguments),
                )
            ],
        )

    class Scripted(Model):
        def __init__(self) -> None:
            super().__init__()
            self.turns = 0

        def generate(self, messages: Any, **kw: Any) -> ChatMessage:
            self.turns += 1
            if self.turns == 1:
                return call(TOOL, dict(args), "c1")
            return call("final_answer", {"answer": "stopped"}, "c2")

    agent = ToolCallingAgent(
        tools=gate_tools(preflight, [GlobTool()]), model=Scripted(), max_steps=3
    )
    agent.run("go")

    observations = [
        str(step.observations) for step in agent.memory.steps if getattr(step, "observations", None)
    ]
    return Driven(ran=bool(ran), sentence=_sentence_in(observations))


def _semantic_kernel(preflight: Preflight, args: dict[str, Any]) -> Driven:
    """Semantic Kernel's auto function-calling loop, over a scripted chat service.

    `SUPPORTS_FUNCTION_CALLING` has to be declared on the client: without it the kernel skips
    automatic invocation entirely, the function is never called, and the row would pass on `ran`
    while proving nothing. That is why this asserts the sentence as well — an untouched tool is not
    evidence when the loop never reached it.
    """
    import asyncio

    from semantic_kernel import Kernel
    from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
    from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
    from semantic_kernel.contents import ChatHistory, ChatMessageContent, FunctionCallContent
    from semantic_kernel.contents.utils.author_role import AuthorRole
    from semantic_kernel.filters import FilterTypes
    from semantic_kernel.functions import kernel_function

    from neti.adapters.semantic_kernel_filters import neti_filter

    ran: list[str] = []
    seen: list[str] = []

    class Plugin:
        @kernel_function(name=TOOL, description="Match files.")
        def glob(self, pattern: str) -> str:
            ran.append(pattern)
            return "ran"

    class Scripted(ChatCompletionClientBase):
        # Un-annotated on purpose: annotating it makes pydantic treat it as a field that shadows
        # the parent's, and the warning is an error here. Without it set at all, the kernel skips
        # automatic invocation and the function is never called — which is why the sentence is
        # asserted below and not just `ran`.
        SUPPORTS_FUNCTION_CALLING = True

        turns: int = 0

        async def _inner_get_chat_message_contents(
            self, chat_history: Any, settings: Any
        ) -> list[Any]:
            self.turns += 1
            seen.append(str(chat_history))
            if self.turns == 1:
                return [
                    ChatMessageContent(
                        role=AuthorRole.ASSISTANT,
                        items=[
                            FunctionCallContent(
                                id="c1",
                                name=f"fs-{TOOL}",
                                plugin_name="fs",
                                function_name=TOOL,
                                arguments=dict(args),
                            )
                        ],
                    )
                ]
            return [ChatMessageContent(role=AuthorRole.ASSISTANT, content="stopped")]

        async def _inner_get_streaming_chat_message_contents(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError

        @property
        def service_id(self) -> str:
            return "scripted"

    kernel = Kernel()
    kernel.add_plugin(Plugin(), plugin_name="fs")
    kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, neti_filter(preflight))
    service = Scripted(ai_model_id="scripted")
    kernel.add_service(service)

    settings = service.get_prompt_execution_settings_class()(
        function_choice_behavior=FunctionChoiceBehavior.Auto()
    )
    history = ChatHistory()
    history.add_user_message("go")

    async def go() -> None:
        await service.get_chat_message_contents(
            chat_history=history, settings=settings, kernel=kernel
        )

    asyncio.run(go())
    return Driven(ran=bool(ran), sentence=_sentence_in(seen))


RUNTIMES: tuple[Runtime, ...] = (
    Runtime(
        "langgraph",
        ("langgraph", "langchain_core"),
        AGENT_LOOP,
        "a compiled StateGraph executing a ToolNode",
        _langgraph,
    ),
    Runtime(
        "langchain",
        ("langchain", "langchain_core"),
        AGENT_LOOP,
        "langchain.agents.create_agent, via the model interface",
        _langchain,
    ),
    Runtime(
        "openai-agents",
        ("agents", "openai"),
        AGENT_LOOP,
        "the OpenAI Agents SDK Runner",
        _openai_agents,
    ),
    Runtime(
        "pydantic-ai",
        ("pydantic_ai",),
        AGENT_LOOP,
        "a Pydantic AI Agent over FunctionModel",
        _pydantic_ai,
    ),
    Runtime(
        "google-adk",
        ("google.adk",),
        AGENT_LOOP,
        "an ADK App run by InMemoryRunner",
        _google_adk,
    ),
    Runtime(
        "autogen",
        ("autogen_core", "autogen_agentchat"),
        AGENT_LOOP,
        "AutoGen AssistantAgent.run over a workbench",
        _autogen,
    ),
    Runtime(
        "anthropic",
        ("anthropic", "httpx"),
        AGENT_LOOP,
        "the Anthropic tool_runner, read off the wire",
        _anthropic,
    ),
    Runtime(
        "crewai",
        ("crewai",),
        AGENT_LOOP,
        "a real Crew.kickoff, read from the agent's observation",
        _crewai,
    ),
    Runtime(
        "llamaindex",
        ("llama_index.core",),
        AGENT_LOOP,
        "a LlamaIndex FunctionAgent over a FunctionCallingLLM",
        _llamaindex,
    ),
    Runtime(
        "smolagents",
        ("smolagents",),
        AGENT_LOOP,
        "a smolagents ToolCallingAgent",
        _smolagents,
    ),
    Runtime(
        "semantic-kernel",
        ("semantic_kernel",),
        AGENT_LOOP,
        "Semantic Kernel's auto function-calling loop",
        _semantic_kernel,
    ),
)
