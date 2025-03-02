from typing import Literal
from langchain_core.messages import ToolMessage, HumanMessage
from langgraph.func import task

from src.schemas import State


@task
def route_after_triage(
    state: dict,
) -> Literal["draft_response", "mark_as_read_node", "notify"]:
    print(f"\n{'='*50}\n route_after_triage \n{'='*50}\n")

    if state["triage"].response == "email":
        return "draft_response"
    elif state["triage"].response == "no":
        return "mark_as_read_node"
    elif state["triage"].response == "notify":
        return "notify"
    elif state["triage"].response == "question":
        return "draft_response"
    else:
        raise ValueError


@task
def take_action_after_draft_response(
    state: State,
) -> Literal[
    "send_message",
    "rewrite",
    "mark_as_read_node",
    "find_meeting_time",
    "send_cal_invite",
    "bad_tool_name",
]:
    prediction = state["messages"][-1]
    # print()
    print("prediction")
    print(prediction)
    print()

    if len(prediction.tool_calls) != 1:
        raise ValueError
    tool_call = prediction.tool_calls[0]
    if tool_call["name"] == "Question":
        return "send_message"
    elif tool_call["name"] == "ResponseEmailDraft":
        return "rewrite"
    elif tool_call["name"] == "Ignore":
        return "mark_as_read_node"
    elif tool_call["name"] == "MeetingAssistant":
        return "find_meeting_time"
    elif tool_call["name"] == "SendCalendarInvite":
        return "send_cal_invite"
    else:
        return "bad_tool_name"


# @task
def action_after_human(
    state,
) -> Literal[
    "mark_as_read_node", "draft_response", "send_email_node", "send_cal_invite_node"
]:
    print("---- action_after_human ----")
    messages = state.get("messages") or []
    if len(messages) == 0:
        print("---- message length is zero ----")
        if state["triage"].response == "notify":
            return "mark_as_read_node"
        raise ValueError
    else:
        print("---- message length is NOT zero ----")
        if isinstance(messages[-1], (ToolMessage, HumanMessage)):
            print("---- last msg is: ToolMessage or HumanMessage ----")
            return "draft_response"
        else:
            execute = messages[-1].tool_calls[0]
            if execute["name"] == "ResponseEmailDraft":
                return "send_email_node"
            elif execute["name"] == "SendCalendarInvite":
                return "send_cal_invite_node"
            elif execute["name"] == "Ignore":
                return "mark_as_read_node"
            elif execute["name"] == "Question":
                return "draft_response"
            else:
                raise ValueError
