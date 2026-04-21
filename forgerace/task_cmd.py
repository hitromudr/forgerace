def list_tasks():
    """Print a compact table of tasks: ID, status, agent, name."""
    from .tasks import parse_tasks
    tasks = parse_tasks()
    if not tasks:
        print("No tasks found.")
        return

    # Header
    print(f"{'ID':<10} {'STATUS':<20} {'AGENT':<15} NAME")
    for t in tasks:
        agent = t.agent if t.agent and t.agent != "—" else "-"
        print(f"{t.id:<10} {t.status:<20} {agent:<15} {t.name}")
