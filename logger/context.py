import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='')
project_var: contextvars.ContextVar[str] = contextvars.ContextVar('project_name', default='')
pipeline_var: contextvars.ContextVar[str] = contextvars.ContextVar('pipeline_iid', default='')
route_var: contextvars.ContextVar[str] = contextvars.ContextVar('route_name', default='')


def set_request_context(request_id=None, route_name=None, project_name=None, pipeline_iid=None):
    if request_id is not None:
        request_id_var.set(request_id)
    if route_name is not None:
        route_var.set(route_name)
    if project_name is not None:
        project_var.set(project_name)
    if pipeline_iid is not None:
        pipeline_var.set(str(pipeline_iid))


def clear_request_context():
    request_id_var.set('')
    route_var.set('')
    project_var.set('')
    pipeline_var.set('')
