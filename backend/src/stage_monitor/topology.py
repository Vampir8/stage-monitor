"""Protocol independent dependency graph and conservative, scope filtered correlation."""

from collections import deque

from fastapi import Depends

from .auth import can_access
from .contracts import now


def validate_graph(devices):
    indegree = dict.fromkeys(devices, 0)
    children = {key: [] for key in devices}
    for device in devices.values():
        seen = set()
        for link in device.dependencies:
            if link.device_id not in devices:
                raise ValueError("Dependency target does not exist")
            if link.device_id in seen:
                raise ValueError("Duplicate dependency target")
            seen.add(link.device_id)
            indegree[device.id] += 1
            children[link.device_id].append(device.id)
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(devices):
        raise ValueError("Dependencies cannot contain cycles or self references")


def link_condition(manager, link, clock):
    parent = manager.devices[link.device_id]
    state = manager.states.get(parent.id)
    if not getattr(manager, 'monitored', lambda d: d.enabled)(parent):
        return "unknown", "disabled"
    if not state or state.health.last_seen is None:
        return "unknown", "never_seen"
    if state.status in {"offline", "error"}:
        return "unavailable", "upstream_unavailable"
    if (
        state.connection_status != "connected"
        or not 0 <= (clock - state.health.last_seen).total_seconds() <= parent.stale_after
    ):
        return "unknown", "stale_connection"
    if link.metric_key:
        metrics = [
            m for m in state.metrics if m.key == link.metric_key and m.channel == link.channel
        ]
        if len(metrics) != 1:
            return "unknown", "port_metric_missing"
        metric = metrics[0]
        if (
            metric.quality != "good"
            or not 0 <= (clock - metric.observed_at).total_seconds() <= parent.stale_after
        ):
            return "unknown", "port_metric_stale"
        # Port names are labels, never inferred protocol addresses or metric keys.
        if metric.value is False or (
            isinstance(metric.value, str) and metric.value.casefold() in {"down", "offline"}
        ):
            return "unavailable", "port_down"
        if metric.value is True or (
            isinstance(metric.value, str) and metric.value.casefold() in {"up", "online"}
        ):
            return "available", "port_up"
        return "unknown", "port_metric_unsupported"
    return "available", "upstream_available"


def project(manager, user):
    clock = now()
    visible = {key: d for key, d in manager.devices.items() if can_access(user, key)}
    edges, incoming = [], {key: [] for key in visible}
    for device in visible.values():
        for link in device.dependencies:
            if link.device_id not in visible:
                continue
            condition, reason = link_condition(manager, link, clock)
            edge = {
                "from": link.device_id,
                "to": device.id,
                **link.model_dump(),
                "condition": condition,
                "reason": reason,
            }
            edges.append(edge)
            incoming[device.id].append(edge)
    nodes = []
    for device in visible.values():
        state = manager.states.get(device.id)
        unavailable = device.enabled and state and state.status in {"offline", "error"}
        causes, checked, queue = [], set(), deque([device.id])
        if unavailable:
            while queue:
                child = queue.popleft()
                for edge in incoming[child]:
                    key = (edge["from"], edge["to"])
                    if key in checked:
                        continue
                    checked.add(key)
                    if edge["condition"] == "unavailable":
                        causes.append(
                            {
                                "device_id": edge["from"],
                                "port": edge["port"],
                                "reason": edge["reason"],
                                "via_device_id": child,
                            }
                        )
                        queue.append(edge["from"])
        nodes.append(
            {
                "id": device.id,
                "name": device.name,
                "category": device.category,
                "rack": device.rack,
                "location": device.location,
                "tags": device.tags,
                "status": str(state.status) if state else "unknown",
                "enabled": device.enabled,
                "possible_causes": causes,
            }
        )
    return {"time": clock, "nodes": nodes, "edges": edges}


def register(app, current_user):
    @app.get("/api/topology")
    async def topology(user=Depends(current_user)):
        return project(app.state.manager, user)
