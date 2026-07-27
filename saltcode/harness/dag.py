from collections.abc import Callable
from typing import Any

import networkx as nx

from saltcode.harness.budget import LoopCapExceededError


class DAGNode:
    """A node in the execution DAG."""

    def __init__(self, name: str, func: Callable[..., Any], loop_cap: int | None = None):
        self.name = name
        self.func = func
        self.loop_cap = loop_cap


class DAG:
    """A directed acyclic graph for orchestrating agent/task execution."""

    def __init__(self) -> None:
        self.graph: Any = nx.DiGraph()  # type: ignore
        self.nodes: dict[str, DAGNode] = {}

    def add_node(self, node: DAGNode) -> None:
        """Adds a node to the DAG."""
        self.nodes[node.name] = node
        self.graph.add_node(node.name)

    def add_edge(self, from_node: str, to_node: str) -> None:
        """Adds a directed edge between two nodes."""
        if from_node not in self.nodes or to_node not in self.nodes:
            raise ValueError("Both nodes must be added to the DAG before creating an edge.")
        self.graph.add_edge(from_node, to_node)
        
        # Check that we haven't introduced cycles
        if not nx.is_directed_acyclic_graph(self.graph):  # type: ignore
            self.graph.remove_edge(from_node, to_node)
            raise ValueError("Adding this edge would create a cycle in the DAG.")

    def get_topological_order(self) -> list[str]:
        """Returns nodes in topological order."""
        return list(nx.topological_sort(self.graph))  # type: ignore


class DAGExecutor:
    """Executes DAG nodes while tracking per-node executions and enforcing loop caps."""

    def __init__(self, dag: DAG, budget_tracker: Any = None) -> None:
        self.dag = dag
        self.budget_tracker = budget_tracker
        self.execution_counts: dict[str, int] = {}

    def run_node(self, node_name: str, *args: Any, **kwargs: Any) -> Any:
        """Runs the callable of the specified node, validating and updating execution counts."""
        if node_name not in self.dag.nodes:
            raise ValueError(f"Node '{node_name}' does not exist in the DAG.")

        node = self.dag.nodes[node_name]
        
        # Track local execution count
        count = self.execution_counts.get(node_name, 0) + 1
        self.execution_counts[node_name] = count

        # If we have a budget tracker, register the loop there to ensure persistence
        if self.budget_tracker is not None:
            if node_name == "architect":
                self.budget_tracker.increment_architect_loop()
            elif node_name == "planner":
                self.budget_tracker.increment_planner_loop()
        elif node.loop_cap is not None and count > node.loop_cap:
            # Fallback to local loop cap validation if no persistent tracker
            raise LoopCapExceededError(
                f"Loop cap of {node.loop_cap} exceeded for node '{node_name}'."
            )

        return node.func(*args, **kwargs)
