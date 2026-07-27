from saltcode.config import settings


def get_agent_tier(agent_name: str, *, is_online: bool, is_fresh_project: bool) -> str:
    """Returns the model/profile name for the agent based on connectivity and project state.

    Offline: All Phase-1 agents (Scout, Architect, Planner, Test Intent, Evaluator) run on local profile 'B'.
    Online:
      - Scout, Planner, Test Intent, Spec Compactor: 'deepseek-chat' (Flash)
      - Architect, Evaluator: 'deepseek-reasoner' (Pro) on fresh projects; 'deepseek-chat' (Flash) on amend/replan.
    """
    name = agent_name.lower().replace(" ", "_").replace("-", "_")

    if not is_online:
        if name in (
            "scout", "architect", "planner", "test_intent", "test-intent",
            "evaluator", "spec_compactor", "compactor"
        ):
            return "B"
        # Phase 2 fallback / default
        return settings.local_default_model

    # Online path
    if name in ("scout", "planner", "test_intent", "test-intent", "spec_compactor", "compactor"):
        return settings.deepseek_chat_model
    if name in ("architect", "evaluator"):
        return settings.deepseek_reasoning_model if is_fresh_project else settings.deepseek_chat_model
    
    # Defaults
    return settings.deepseek_chat_model


def should_escalate_auditor(stability_score: float, threshold: float, *, is_online: bool) -> bool:
    """Determines whether the Auditor faithfulness judgment should escalate to Flash online.

    If offline, the faithfulness judgment stays local (returns False).
    If online and stability_score < threshold, it escalates to Flash (returns True).
    """
    if not is_online:
        return False
    return stability_score < threshold
