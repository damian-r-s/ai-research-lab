"""Run lifecycle: start, pause, resume, stop, the kill switch and run dispatch (ARCHITECTURE §6.6).

Framework-free: no LangGraph here. The graph runtime (src.agents) and the runner process
(src.services) call this package to record what actually happened.
"""
