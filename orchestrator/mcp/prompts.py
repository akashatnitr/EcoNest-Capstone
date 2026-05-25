"""Reusable reasoning prompt templates."""

ENERGY_REVIEW_PROMPT = """
Review the last 24h of energy usage and identify:
- off-schedule devices
- unusual consumption
- possible optimization opportunities
"""

SECURITY_CHECK_PROMPT = """
Analyze recent motion, entry, and occupancy signals.
Identify:
- suspicious patterns
- unusual access behavior
- possible security anomalies
"""

DEVICE_CONTROL_PROMPT = """
The user wants to control a smart-home device.
Clarify ambiguous instructions before taking action.
"""

SENSOR_HEALTH_PROMPT = """
Review sensor reporting behavior and identify:
- stale sensors
- inconsistent reporting
- low-confidence observations
"""
