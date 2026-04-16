def update_task_status(task):
    # TO DO: implement update task status logic
    # TO DO: implement update task status logic
task.rework_count += 1
task.last_attempts.append({'status': 'updated', 'timestamp': datetime.now()})