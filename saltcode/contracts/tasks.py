from typing import Literal

import networkx as nx
from pydantic import BaseModel, Field, model_validator


class Task(BaseModel):
    id: str
    description: str
    files_affected: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    complexity: Literal["low", "med", "high"]

class TasksFile(BaseModel):
    schema_version: str = Field(default="1")
    tasks: list[Task] = []

    @model_validator(mode="after")
    def validate_dependencies(self) -> "TasksFile":
        task_ids = {t.id for t in self.tasks}
        
        # Check dangling references
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    raise ValueError(
                        f"Dangling reference: task '{task.id}' depends on non-existent task '{dep}'"
                    )
        
        # Check acyclicity
        g: nx.DiGraph[str] = nx.DiGraph()  # type: ignore
        for task in self.tasks:
            g.add_node(task.id)
        for task in self.tasks:
            for dep in task.depends_on:
                g.add_edge(dep, task.id)
        
        if not nx.is_directed_acyclic_graph(g):  # type: ignore
            try:
                from typing import cast
                cycle = cast(list[tuple[str, str]], list(nx.find_cycle(g)))  # type: ignore
                nodes = [str(u) for u, _ in cycle]
                cycle_str = " -> ".join(nodes) + f" -> {nodes[0]}"
            except Exception:
                cycle_str = "unknown cycle"
            raise ValueError(f"Dependency cycle detected in tasks: {cycle_str}")
            
        return self
