"""催化组约简使用的小规模精确最小集合覆盖求解器。"""

from __future__ import annotations

from math import ceil


def _greedy_cover(universe_mask, candidates):
    uncovered = universe_mask
    selected = []
    remaining = list(candidates)
    while uncovered:
        best = max(
            remaining,
            key=lambda candidate: (
                (candidate["mask"] & uncovered).bit_count(),
                candidate["size"],
                str(candidate["key"]),
            ),
        )
        gain = best["mask"] & uncovered
        if not gain:
            break
        selected.append(best)
        uncovered &= ~best["mask"]
        remaining.remove(best)
    return selected, uncovered


def _remove_dominated(candidates):
    ordered = sorted(
        candidates,
        key=lambda candidate: (-candidate["size"], str(candidate["key"])),
    )
    kept = []
    for candidate in ordered:
        if any(
            candidate["mask"] & ~other["mask"] == 0
            for other in kept
        ):
            continue
        kept.append(candidate)
    return kept


def minimum_set_cover(universe, candidates):
    """返回基数最小且结果确定的集合覆盖。

    candidates 是包含 key 与 coverage 的映射序列。
    返回值为 (selected_keys, stats)。求解器使用支配约简、必选候选传播、
    贪心上界与剩余全集上的分支限界，保证精确求解。
    """

    elements = list(dict.fromkeys(universe))
    element_bits = {element: index for index, element in enumerate(elements)}
    universe_mask = (1 << len(elements)) - 1

    prepared = []
    for raw in candidates:
        coverage = set(raw.get("coverage", ()))
        mask = 0
        for element in coverage:
            bit = element_bits.get(element)
            if bit is not None:
                mask |= 1 << bit
        if mask:
            prepared.append(
                {
                    "key": raw["key"],
                    "mask": mask,
                    "size": mask.bit_count(),
                }
            )

    original_count = len(prepared)
    prepared = _remove_dominated(prepared)

    if not universe_mask:
        return [], {
            "optimal": True,
            "candidate_count": original_count,
            "reduced_candidate_count": 0,
            "search_nodes": 0,
            "greedy_count": 0,
        }

    greedy_selected, greedy_uncovered = _greedy_cover(universe_mask, prepared)
    if greedy_uncovered:
        raise ValueError("candidate groups do not cover the requested universe")

    best = list(greedy_selected)
    best_count = len(best)
    seen_depth = {}
    search_nodes = 0

    # 能唯一覆盖任一剩余元素的候选是必选项。
    mandatory = []
    uncovered = universe_mask
    while True:
        unique = None
        for bit in range(len(elements)):
            element_mask = 1 << bit
            if not (uncovered & element_mask):
                continue
            covering = [item for item in prepared if item["mask"] & element_mask]
            if len(covering) == 1:
                unique = covering[0]
                break
        if unique is None:
            break
        mandatory.append(unique)
        uncovered &= ~unique["mask"]
        prepared = [item for item in prepared if item is not unique]

    base = list(mandatory)
    if uncovered:
        extra, leftover = _greedy_cover(uncovered, prepared)
        if leftover:
            raise ValueError("candidate groups do not cover the requested universe")
        candidate_solution = base + extra
        if len(candidate_solution) < best_count:
            best = candidate_solution
            best_count = len(best)
    else:
        best = base
        best_count = len(best)

    def lower_bound(mask):
        if not mask:
            return 0
        max_gain = max(
            (candidate["mask"] & mask).bit_count() for candidate in prepared
        )
        if not max_gain:
            return 1 << 30
        return ceil(mask.bit_count() / max_gain)

    def search(mask, chosen):
        nonlocal best, best_count, search_nodes
        search_nodes += 1
        if not mask:
            if len(chosen) < best_count:
                best = list(chosen)
                best_count = len(chosen)
            return
        if len(chosen) + lower_bound(mask) >= best_count:
            return
        previous_depth = seen_depth.get(mask)
        if previous_depth is not None and previous_depth <= len(chosen):
            return
        seen_depth[mask] = len(chosen)

        # 选择覆盖元素最少的元素进行分支。这是标准的精确集合覆盖分支规则，
        # 能让搜索在本项目的图上保持较小规模。
        candidate_options = None
        for bit in range(len(elements)):
            element_mask = 1 << bit
            if not (mask & element_mask):
                continue
            options = [item for item in prepared if item["mask"] & element_mask]
            if candidate_options is None or len(options) < len(candidate_options):
                candidate_options = options
                if len(options) == 1:
                    break
        options = sorted(
            candidate_options or [],
            key=lambda item: (
                -(item["mask"] & mask).bit_count(),
                str(item["key"]),
            ),
        )
        for candidate in options:
            gain = candidate["mask"] & mask
            if not gain:
                continue
            chosen.append(candidate)
            search(mask & ~candidate["mask"], chosen)
            chosen.pop()

    search(uncovered, base)
    return [candidate["key"] for candidate in best], {
        "optimal": True,
        "candidate_count": original_count,
        "reduced_candidate_count": len(prepared) + len(mandatory),
        "search_nodes": search_nodes,
        "greedy_count": len(greedy_selected),
    }
