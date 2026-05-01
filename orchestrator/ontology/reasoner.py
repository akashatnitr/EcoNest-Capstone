"""Forward chaining reasoner using Gremlin traversals."""

from orchestrator.core.database import arcadedb_query


async def run_reasoner() -> dict:
    """Run forward chaining rules and return inferred triples summary.

    Rules:
    1. If device type is SmartBulb, infer hasCapability Dimmable.
    2. If device type is MotionSensor and locatedIn Room, infer monitors Room.
    3. If user role is family_member and room_type is Bedroom, infer CAN_PERFORM TurnOn.
    """
    inferred: list[dict] = []

    # Rule 1: SmartBulb -> hasCapability Dimmable
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device').has('device_type', 'SmartBulb').as('device')"
            ".not(outE('HAS_CAPABILITY').inV().has('name', 'Dimmable'))"
            ".select('device').values('@rid')"
        ),
    )
    for rid in result.get("result", []):
        await arcadedb_query(
            "sql",
            (
                f"CREATE EDGE HAS_CAPABILITY FROM {rid} TO "
                f"(SELECT FROM Capability WHERE name = 'Dimmable')"
            ),
            readonly=False,
        )
        inferred.append({"rule": 1, "device": rid, "capability": "Dimmable"})

    # Rule 2: MotionSensor + locatedIn Room -> monitors Room
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device').has('device_type', 'MotionSensor')"
            ".outE('LOCATED_IN').inV().hasLabel('Room').as('room')"
            ".select('room').path().by(values('@rid'))"
        ),
    )
    for path in result.get("result", []):
        if isinstance(path, dict) and "objects" in path:
            objects = path["objects"]
            if len(objects) >= 2:
                device_rid = objects[0]
                room_rid = objects[-1]
                await arcadedb_query(
                    "sql",
                    f"CREATE EDGE MONITORS FROM {device_rid} TO {room_rid}",
                    readonly=False,
                )
                inferred.append({"rule": 2, "device": device_rid, "room": room_rid})

    # Rule 3: family_member + Bedroom -> CAN_PERFORM TurnOn for devices in that room
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('User').has('role', 'family_member').as('user')"
            ".outE('HAS_ACCESS').inV().hasLabel('Room').has('room_type', 'Bedroom')"
            ".in('LOCATED_IN').hasLabel('Device').as('device')"
            ".select('user','device').by(values('@rid'))"
        ),
    )
    for pair in result.get("result", []):
        user_rid = pair.get("user")
        device_rid = pair.get("device")
        if user_rid and device_rid:
            await arcadedb_query(
                "sql",
                f"CREATE EDGE CAN_PERFORM FROM {user_rid} TO {device_rid}",
                readonly=False,
            )
            inferred.append({"rule": 3, "user": user_rid, "device": device_rid})

    return {"inferred": inferred, "total": len(inferred)}
