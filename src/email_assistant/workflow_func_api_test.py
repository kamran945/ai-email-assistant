from langgraph.func import entrypoint
from langgraph.graph import add_messages
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import StreamWriter
from langchain_core.messages import ToolMessage, AnyMessage
from langgraph.func import task
from typing import List, Literal, Optional
from typing_extensions import TypedDict
import asyncio
from langsmith import traceable
import json

from src.email_assistant.configuration import Configuration
from src.email_assistant.triage_task import triage_input
from src.email_assistant.routers import (
    route_after_triage,
    take_action_after_draft_response,
    action_after_human,
)
from src.gmail import mark_as_read, send_email
from src.schemas import TriageInput, State
from src.email_assistant.human_inbox import (
    notify,
    send_message,
    send_email_draft,
    send_cal_invite,
)
from src.email_assistant.draft_response import draft_response
from src.email_assistant.rewrite_email import rewrite
from src.email_assistant.find_meeting_time import find_meeting_time
from src.gmail import send_calendar_invite


# checkpointer = MemorySaver()
# store = InMemoryStore()
from src.email_assistant.checkpointer import checkpointer, store


async def add_messages_to_state(
    state_messages: List[AnyMessage], messages_to_add: List[AnyMessage]
) -> List[AnyMessage]:
    print("---- add_messages_to_state ----")
    print(f"state msgs: {state_messages}")
    print()
    print(f"msgs to add: {messages_to_add}")
    print()
    messages = (
        add_messages(state_messages, messages_to_add)
        if len(messages_to_add) > 0
        else state_messages
    )
    return messages


@traceable
def bad_tool_name(state: State):
    print("---- bad_tool_name ----")
    tool_call = state["messages"][-1].tool_calls[0]
    message = f"Could not find tool with name `{tool_call['name']}`. Make sure you are calling one of the allowed tools!"
    last_message = state["messages"][-1]
    last_message.tool_calls[0]["name"] = last_message.tool_calls[0]["name"].replace(
        ":", ""
    )
    return {
        "messages": [
            last_message,
            ToolMessage(content=message, tool_call_id=tool_call["id"]),
        ]
    }


@traceable
def send_cal_invite_node(state, config):
    print("---- send_cal_invite_node ----")
    tool_call = state["messages"][-1].tool_calls[0]
    _args = tool_call["args"]
    # email = get_config(config)["email"]
    configuration = Configuration.from_runnable_config(config=config)
    email = configuration.config_yaml["email"]
    try:
        send_calendar_invite(
            _args["emails"],
            _args["title"],
            _args["start_time"],
            _args["end_time"],
            email,
        )
        message = "Sent calendar invite!"
    except Exception as e:
        message = f"Got the following error when sending a calendar invite: {e}"
    return {"messages": [ToolMessage(content=message, tool_call_id=tool_call["id"])]}


@traceable
def send_email_node(state, config):
    print("---- send_email_node ----")
    configuration = Configuration.from_runnable_config(config=config)
    prompt_config = configuration.config_yaml

    tool_call = state["messages"][-1].tool_calls[0]
    _args = tool_call["args"]

    email = prompt_config["email"]
    new_receipients = _args["new_recipients"]

    if isinstance(new_receipients, str):
        new_receipients = json.loads(new_receipients)
    send_email(
        state["email"]["id"],
        _args["content"],
        email,
        addn_receipients=new_receipients,
    )


@entrypoint()
async def triage_workflow(input: State, config: RunnableConfig, *, previous):
    """triage_workflow"""
    print(f"\n{'='*50}\n triage_workflow \n{'='*50}\n")
    # previous = previous or []
    # messages = add_messages(previous, input["messages"])
    # input["messages"] = messages

    triage_out = await triage_input(state=input, config=config, store=store)
    print(triage_out)
    # messages = add_messages(input["messages"], triage_out["messages"])
    input["messages"] = await add_messages_to_state(
        state_messages=input["messages"], messages_to_add=triage_out["messages"]
    )
    after_triage = await route_after_triage(state=triage_out)

    return {
        "triage": triage_out["triage"],
        "after_triage": after_triage,
        "messages": input["messages"],
    }


@entrypoint()
async def draft_response_workflow(
    input: State, config: RunnableConfig, *, previous
) -> str:
    """draft_response_workflow"""
    print(f"\n{'='*50}\n draft_response_workflow \n{'='*50}\n")

    draft_response_out = await draft_response(state=input, config=config, store=store)
    print(draft_response_out)

    input["messages"] = await add_messages_to_state(
        state_messages=input["messages"], messages_to_add=draft_response_out["messages"]
    )
    after_draft_response = await take_action_after_draft_response(state=input)

    return {
        "after_draft_response": after_draft_response,
        "messages": input["messages"],
    }


class ProcessTaskInput(TypedDict):
    next_task: str
    state: State


@task
async def _process_task(input: ProcessTaskInput, config: RunnableConfig) -> dict:
    """Process a single workflow task"""
    if input["next_task"] == "END":
        return {"state": input["state"], "next_task": "END"}

    print(f"Processing task: {input['next_task']}")

    if input["next_task"] == "mark_as_read_node":
        print("---- mark_as_read_node ----")
        mark_as_read(input["state"]["email"]["id"])
        input["next_task"] = "END"
    elif input["next_task"] == "notify":
        print("---- notify ----")
        notify_output = await notify(state=input["state"], config=config, store=store)
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=notify_output["messages"],
        )
        input["next_task"] = "human_node"
    elif input["next_task"] == "draft_response":
        print("---- draft_response ----")
        after_draft_output = await draft_response_workflow.ainvoke(
            input=input["state"], config=config
        )
        input["state"]["messages"] = after_draft_output["messages"]
        print()
        print(f"---- after_draft_response: ----\n{after_draft_output}")
        print()
        input["next_task"] = after_draft_output["after_draft_response"]
        # next_task = "send_message"
    elif input["next_task"] == "bad_tool_name":
        print("---- bad_tool_name ----")
        bad_tool_name_output = await bad_tool_name(state=input["state"], config=config)
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=bad_tool_name_output["messages"],
        )
        input["next_task"] = "draft_response"
    elif input["next_task"] == "find_meeting_time":
        print("---- find_meeting_time ----")
        find_meeting_time_output = await find_meeting_time(
            state=input["state"], config=config
        )
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=find_meeting_time_output["messages"],
        )
        input["next_task"] = "draft_response"
    elif input["next_task"] == "send_message":
        print("---- send_message ----")
        send_message_output = await send_message(
            state=input["state"], config=config, store=store
        )
        print(input["state"])
        print(send_message_output)
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=send_message_output["messages"],
        )
        input["next_task"] = "human_node"
    elif input["next_task"] == "send_cal_invite_node":
        print("---- send_cal_invite_node ----")
        send_cal_invite_node_output = await send_cal_invite_node(
            state=input["state"], config=config
        )
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=send_cal_invite_node_output["messages"],
        )
        input["next_task"] = "draft_reponse"
    elif input["next_task"] == "send_cal_invite":
        print("---- send_cal_invite ----")
        send_cal_invite_output = await send_cal_invite(
            state=input["state"], config=config, store=store
        )
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=send_cal_invite_output["messages"],
        )
        input["next_task"] = "human_node"
    elif input["next_task"] == "rewrite":
        print("---- rewrite ----")
        rewrite_output = await rewrite(state=input["state"], config=config, store=store)
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=rewrite_output["messages"],
        )
        input["next_task"] = "send_email_draft"
    elif input["next_task"] == "send_email_draft":
        print("---- send_email_draft ----")
        send_email_draft_output = await send_email_draft(
            state=input["state"], config=config, store=store
        )
        input["state"]["messages"] = await add_messages_to_state(
            state_messages=input["state"]["messages"],
            messages_to_add=send_email_draft_output["messages"],
        )
        input["next_task"] = "human_node"
    elif input["next_task"] == "send_email_node":
        print("---- send_email_node ----")
        await send_email_node(state=input["state"], config=config, store=store)
        input["next_task"] = "mark_as_read_node"
    elif input["next_task"] == "human_node":
        print("---- human_node ----")
        after_human_out = action_after_human(state=input["state"])
        print(f"---- after_human_out: {after_human_out} ----")
        if after_human_out == "mark_as_read_node":
            print("---- after_human_out mark_as_read_node ----")
            input["next_task"] = "mark_as_read_node"
        elif after_human_out == "draft_response":
            print("---- after_human_out draft_response ----")
            input["next_task"] = "draft_response"
        elif after_human_out == "send_email_node":
            print("---- after_human_out send_email_node ----")
            input["next_task"] = "send_email_node"
        elif after_human_out == "send_cal_invite_node":
            print("---- after_human_out send_cal_invite_node ----")
            input["next_task"] = "send_cal_invite_node"

    print()
    print("returning values in _process_task..............")
    print()
    print(f"state: {input['state']}")
    print()
    print(f"next_task: {input['next_task']}")
    print()
    return {"state": input["state"], "next_task": input["next_task"]}


@entrypoint(checkpointer=checkpointer, store=store)
async def email_assistant_workflow(
    state: State,
    config: RunnableConfig,
    store=store,
    *,
    previous: Optional[dict] = None,
) -> dict:
    """AI Email Assistant workflow with interruptible loop"""
    print(f"\n{'='*50}\n email_assistant_workflow \n{'='*50}\n")

    # Restore state from checkpoint (if resuming)
    if previous:
        print("restore previous state...............")
        state = previous["state"]
        next_task = previous["next_task"]
    else:
        print("initial setup........")
        # Initial state setup
        triage_out = await triage_workflow.ainvoke(input=state, config=config)
        state["messages"] = triage_out["messages"]
        state["triage"] = triage_out["triage"]
        next_task = triage_out["after_triage"]

    # Process one task per invocation (checkpoint after each iteration)
    while next_task != "END":
        task_result = await _process_task(
            input={"state": state, "next_task": next_task}, config=config
        )
        print()
        print("---- task result: ", task_result, " ----")
        print()
        state = task_result["state"]
        next_task = task_result["next_task"]

    # Return state and next_task for checkpointing
    return entrypoint.final(
        value={"state": state, "next_task": next_task},
        save={"state": state, "next_task": next_task},
    )


# @entrypoint(checkpointer=checkpointer, store=store)
# async def email_assistant_workflow(
#     state: State, config: RunnableConfig, store: BaseStore, *, previous
# ) -> dict:
#     """AI Email Assistant workflow"""
#     print(f"\n{'='*50}\n email_assistant_workflow \n{'='*50}\n")
#     previous = previous or []
#     # if len(previous) > 0:
#     #     print("---- previous workflow state ----")
#     #     print(f"\n{'='*50}\n {previous} \n{'='*50}\n")
#     #     # state["messages"] = await add_messages_to_state(
#     #     #     state["messages"], previous["messages"]
#     #     # )
#     #     state = {**state, **previous}

#     print(f"workflow_input: {state}")
#     # print(f"workflow_input incl previous: {messages}")
#     configuration = Configuration.from_runnable_config(config=config)

#     after_triage = await triage_workflow.ainvoke(input=state, config=config)
#     state["messages"] = after_triage["messages"]
#     state["triage"] = after_triage["triage"]
#     print()
#     print(f"after_triage: {state['messages']}")
#     print()
#     next_task = after_triage["after_triage"]
#     print(f"next_task after_triage: {next_task}")

#     # testing................................................................
#     # next_task = "draft_response"

#     while next_task != "END":
#         if next_task == "mark_as_read_node":
#             print("---- mark_as_read_node ----")
#             mark_as_read(state["email"]["id"])
#             next_task = "END"
#         elif next_task == "notify":
#             print("---- notify ----")
#             notify_output = await notify(state=state, config=config, store=store)
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=notify_output["messages"],
#             )
#             next_task = "human_node"
#         elif next_task == "draft_response":
#             print("---- draft_response ----")
#             after_draft_output = await draft_response_workflow.ainvoke(
#                 input=state, config=config
#             )
#             state["messages"] = after_draft_output["messages"]
#             print()
#             print(f"---- after_draft_response: ----\n{after_draft_output}")
#             print()
#             next_task = after_draft_output["after_draft_response"]
#             # next_task = "send_message"
#         elif next_task == "bad_tool_name":
#             print("---- bad_tool_name ----")
#             bad_tool_name_output = await bad_tool_name(state=state, config=config)
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=bad_tool_name_output["messages"],
#             )
#             next_task = "draft_response"
#         elif next_task == "find_meeting_time":
#             print("---- find_meeting_time ----")
#             find_meeting_time_output = await find_meeting_time(
#                 state=state, config=config
#             )
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=find_meeting_time_output["messages"],
#             )
#             next_task = "draft_response"
#         elif next_task == "send_message":
#             print("---- send_message ----")
#             send_message_output = await send_message(
#                 state=state, config=config, store=store
#             )
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=send_message_output["messages"],
#             )
#             next_task = "human_node"
#         elif next_task == "send_cal_invite_node":
#             print("---- send_cal_invite_node ----")
#             send_cal_invite_node_output = await send_cal_invite_node(
#                 state=state, config=config
#             )
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=send_cal_invite_node_output["messages"],
#             )
#             next_task = "draft_reponse"
#         elif next_task == "send_cal_invite":
#             print("---- send_cal_invite ----")
#             send_cal_invite_output = await send_cal_invite(
#                 state=state, config=config, store=store
#             )
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=send_cal_invite_output["messages"],
#             )
#             next_task = "human_node"
#         elif next_task == "rewrite":
#             print("---- rewrite ----")
#             rewrite_output = await rewrite(state=state, config=config, store=store)
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=rewrite_output["messages"],
#             )
#             next_task = "send_email_draft"
#         elif next_task == "send_email_draft":
#             print("---- send_email_draft ----")
#             send_email_draft_output = await send_email_draft(
#                 state=state, config=config, store=store
#             )
#             state["messages"] = await add_messages_to_state(
#                 state_messages=state["messages"],
#                 messages_to_add=send_email_draft_output["messages"],
#             )
#             next_task = "human_node"
#         elif next_task == "send_email_node":
#             print("---- send_email_node ----")
#             await send_email_node(state=state, config=config, store=store)
#             next_task = "mark_as_read_node"
#         elif next_task == "human_node":
#             print("---- human_node ----")
#             after_human_out = await action_after_human(state=state)
#             print(f"---- after_human_out: {after_human_out} ----")
#             if after_human_out == "mark_as_read_node":
#                 print("---- after_human_out mark_as_read_node ----")
#                 next_task = "mark_as_read_node"
#             elif after_human_out == "draft_response":
#                 print("---- after_human_out draft_response ----")
#                 next_task = "draft_response"
#             elif after_human_out == "send_email_node":
#                 print("---- after_human_out send_email_node ----")
#                 next_task = "send_email_node"
#             elif after_human_out == "send_cal_invite_node":
#                 print("---- after_human_out send_cal_invite_node ----")
#                 next_task = "send_cal_invite_node"

#     return state
