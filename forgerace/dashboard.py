class Dashboard:
    def __init__(self, task_queue, agents_data):
        self.task_queue = task_queue
        self.agents_data = agents_data
        self.table = []

    def update(self):
        # Update the table with the latest data
        self.table = []
        for task in self.task_queue.tasks:
            self.table.append([task.name, task.status])
        for agent in self.agents_data:
            self.table.append([agent.name, agent.time, agent.files, agent.costs])
        # Print the table using rich.live
        from rich.live import Live
        from rich.table import Table
        table = Table(title="Dashboard")
        table.add_column("Name", style="cyan")
        table.add_column("Status", style="magenta")
        table.add_column("Time", style="green")
        table.add_column("Files", style="yellow")
        table.add_column("Costs", style="red")
        for row in self.table:
            table.add_row(*row)
        Live.refresh(table)