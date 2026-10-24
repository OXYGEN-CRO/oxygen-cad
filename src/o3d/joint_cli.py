from __future__ import annotations
import json
from pathlib import Path
from uuid import UUID
import typer
from . import joints

app = typer.Typer(help='Inspect sourced actuator contracts and evaluate persisted static joint requirements.')
def emit(value): typer.echo(json.dumps(value,indent=2,allow_nan=False))

def read(path):
    if path.stat().st_size > 1024 * 1024: raise ValueError('Joint input exceeds 1 MiB')
    value=json.loads(path.read_text())
    if not isinstance(value,dict): raise ValueError('Joint input must be an object')
    return value

@app.command('library')
def library(json_output: bool=typer.Option(False,'--json')):
    emit({'components':[c.model_dump(mode='json') for c in joints.library()]})

@app.command('example')
def example(json_output: bool=typer.Option(False,'--json')):
    emit(joints.example())

@app.command('configure')
def configure(ctx:typer.Context, input:Path, expected_revision:int=typer.Option(...), actor:str='agent', json_output:bool=typer.Option(False,'--json')):
    emit(joints.configure(ctx.obj,read(input),expected_revision,actor).model_dump(mode='json'))

@app.command('evaluate')
def evaluate(ctx:typer.Context, expected_revision:int=typer.Option(...), json_output:bool=typer.Option(False,'--json')):
    result=joints.evaluate(ctx.obj,expected_revision);emit(result)
    if result['report']['state'] != 'passed': raise typer.Exit(2)

@app.command('status')
def status(ctx:typer.Context,json_output:bool=typer.Option(False,'--json')):emit(joints.status(ctx.obj))

@app.command('import-catalog')
def import_catalog(ctx:typer.Context,part_id:UUID,expected_revision:int=typer.Option(...),actor:str='agent',json_output:bool=typer.Option(False,'--json')):
    from .catalog.db import Catalog
    with Catalog() as db: row=db.get(str(part_id))
    if not row: raise ValueError('Catalog part does not exist')
    contract=joints.contract_from_catalog(row)
    project=ctx.obj.load()
    components=project.engineering_brief.components if project.engineering_brief else []
    components=[c for c in components if c.id != contract.id]+[contract]
    emit(joints.configure(ctx.obj,{'components':[c.model_dump(mode='json') for c in components]},expected_revision,actor).model_dump(mode='json'))
