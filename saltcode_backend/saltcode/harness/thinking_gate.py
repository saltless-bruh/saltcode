def get_thinking_mode(
    agent_name: str,
    *,
    is_reinvoked: bool = False,
    is_retry: bool = False,
    is_gaming_suspected: bool = False
) -> bool:
    """Returns the thinking mode status for a given agent and execution state.

    Architect: thinking ON (design reasoning)
    Evaluator: thinking ON only when re-invoked after a fail, else OFF
    Auditor: thinking ON only on retry / gaming_suspected, else OFF
    All others: thinking OFF
    """
    name = agent_name.lower().replace(" ", "_").replace("-", "_")
    if name == "architect":
        return True
    if name == "evaluator":
        return is_reinvoked
    if name == "auditor":
        return is_retry or is_gaming_suspected
    return False
