"""多轮随机化 DSATUR 着色策略。"""

from s6_color.graph_coloring import color_graph
from s6_color.strategies.base import ColorContext


class DSATURMultistartStrategy:
    """封装现有多轮随机化 DSATUR 实现。"""

    name = "dsatur_multistart"

    def solve(self, context: ColorContext):
        graph = {
            "nodes": list(context.nodes),
            "adjacency_matrix": [list(row) for row in context.adjacency_matrix],
        }
        return color_graph(
            graph,
            context.rounds,
            context.max_class_size,
            list(context.node_coverages),
            size_sweep=context.size_sweep,
            sweep_max_size=context.sweep_max_size,
        )
