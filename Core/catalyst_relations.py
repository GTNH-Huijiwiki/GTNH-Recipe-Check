"""Canonical catalyst coverage relations."""

RELATION_SCHEMA = {
    "equivalent": "left_coverage == right_coverage",
    "strict_subset": "left_coverage is a strict subset of right_coverage",
    "strict_superset": "left_coverage is a strict superset of right_coverage",
    "intersect": "coverage sets overlap but neither contains the other",
    "disjoint": "coverage sets have no recipe in common",
}


def classify_relation(left_coverage, right_coverage):
    """Classify the coverage relation from the left node's perspective."""

    left = set(left_coverage)
    right = set(right_coverage)
    if left == right:
        return "equivalent"
    if left < right:
        return "strict_subset"
    if left > right:
        return "strict_superset"
    if left & right:
        return "intersect"
    return "disjoint"


def pure_containment_chains(metadata):
    """List bottom-to-root strict-subset chains ending at top-level classes.

    Only strict-subset edges are followed. Intersect edges never participate,
    so every returned path is a pure containment chain.
    """

    classes = {
        record["id"]: record
        for record in metadata.get("equivalence_classes", [])
    }
    subset_edges = {class_id: set() for class_id in classes}
    incoming = {class_id: set() for class_id in classes}
    for relation in metadata.get("relations", []):
        relation_type = relation.get("relation")
        if relation_type == "strict_subset":
            small, large = relation["left"], relation["right"]
        elif relation_type == "strict_superset":
            small, large = relation["right"], relation["left"]
        else:
            continue
        subset_edges[small].add(large)
        incoming[large].add(small)

    roots = {
        class_id
        for class_id, record in classes.items()
        if record.get("top_level")
    }
    bottoms = {
        class_id
        for class_id, record in classes.items()
        if not record.get("top_level") and not incoming[class_id]
    }

    chains = set()

    def walk(node, path):
        if node in roots:
            chain = tuple(reversed(path))
            if len(chain) >= 2:
                chains.add(chain)
            return
        for parent in sorted(subset_edges[node]):
            if parent not in path:
                walk(parent, path + [parent])

    for bottom in sorted(bottoms):
        walk(bottom, [bottom])

    # For each top/bottom pair keep only the longest path(s); shorter paths
    # are transitive sub-chains and would duplicate the same containment fact.
    max_lengths = {}
    for chain in chains:
        pair = (chain[0], chain[-1])
        max_lengths[pair] = max(max_lengths.get(pair, 0), len(chain))
    chains = {
        chain
        for chain in chains
        if len(chain) == max_lengths[(chain[0], chain[-1])]
    }

    result = []
    for chain in sorted(chains):
        top = chain[0]
        bottom = chain[-1]
        result.append(
            {
                "top": top,
                "bottom": bottom,
                "chain": list(chain),
                "top_coverage_size": classes[top].get("coverage_size", 0),
                "bottom_coverage_size": classes[bottom].get("coverage_size", 0),
                "top_labels": classes[top].get("member_labels", []),
                "bottom_labels": classes[bottom].get("member_labels", []),
            }
        )
    return result


def containment_markdown(relation_reports):
    """Render a human-readable Markdown report of pure containment chains."""

    lines = [
        "# 催化组纯包含链",
        "",
        "只列出最底层等价类到最顶层根组的严格子集链；相交关系不参与。",
        "",
    ]
    has_content = False
    for report in relation_reports:
        chains = report.get("pure_containment_chains", [])
        if not chains:
            continue
        has_content = True
        lines.extend([f"## {report['machine']}", ""])
        lines.append("| 顶层根组 | 底层组 | 链 |")
        lines.append("|---|---|---|")
        for chain in chains:
            path = " ⊃ ".join(chain["chain"])
            lines.append(
                f"| {chain['top']} | {chain['bottom']} | {path} |"
            )
        lines.append("")
    if not has_content:
        lines.append("没有满足条件的纯包含链。")
        lines.append("")
    return "\n".join(lines)


def machine_relation_report(machine_name, metadata):
    """Return the stable JSON-facing relation report for one machine."""

    return {
        "machine": machine_name,
        "relation_schema": RELATION_SCHEMA,
        "groups": metadata.get("group_nodes", []),
        "equivalence_classes": metadata.get("equivalence_classes", []),
        "relations": metadata.get("relations", []),
        "pure_containment_chains": pure_containment_chains(metadata),
    }
