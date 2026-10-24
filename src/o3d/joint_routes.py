from __future__ import annotations
import json
from fastapi import Request, HTTPException
from pydantic import Field
from .joint_models import JointModel, JointRequirement, ActuatorContract
from . import joints

class Configure(JointModel):
    expected_revision: int = Field(ge=1, strict=True)
    actor: str = 'agent'
    joint_requirements: list[JointRequirement] | None = None
    components: list[ActuatorContract] | None = None

class Evaluate(JointModel):
    expected_revision: int = Field(ge=1, strict=True)

async def bounded(request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 1024 * 1024: raise HTTPException(413, 'Joint input exceeds 1 MiB')
    value = json.loads(content)
    if not isinstance(value, dict): raise ValueError('Joint input must be an object')
    return value

def mount(app, store):
    @app.get('/api/joints/library')
    def library():
        return {'components': [c.model_dump(mode='json') for c in joints.library()]}

    @app.get('/api/joints/example')
    def example():
        return joints.example()

    @app.get('/api/joints/status')
    def status():
        return joints.status(store)

    @app.post('/api/joints/configure')
    async def configure(request: Request):
        from starlette.concurrency import run_in_threadpool
        value = Configure.model_validate(await bounded(request))
        if value.actor not in ('agent', 'human'): raise ValueError('Actor must be agent or human')
        data = value.model_dump(mode='json', exclude_none=True, exclude={'actor', 'expected_revision'})
        project = await run_in_threadpool(joints.configure, store, data, value.expected_revision, value.actor)
        return {'project': project.model_dump(mode='json'), 'selection': joints.status(store)}

    @app.post('/api/joints/evaluate')
    async def evaluate(request: Request):
        from starlette.concurrency import run_in_threadpool
        value = Evaluate.model_validate(await bounded(request))
        return await run_in_threadpool(joints.evaluate, store, value.expected_revision)
