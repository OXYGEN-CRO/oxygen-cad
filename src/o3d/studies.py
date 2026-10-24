"""Revision-checked study edits, shared by CLI and document-based clients."""
from .model import Command, Study


def edit_study(store, action, study_id, expected_revision, actor="agent", name=None, kind=None, source=None):
    project = store.load().model_copy(deep=True)
    if action in ("create", "clone"):
        if study_id == "default" or study_id in project.named_studies:
            raise ValueError("Choose a new, non-default study ID")
        if action == "clone":
            study = project.study_config(source).model_copy(deep=True)
            study.assumptions_confirmed = False
        else:
            study = Study(type=kind or "linear_static")
        study.name = name or study_id
        project.named_studies[study_id] = study
        project.active_study_id = study_id
    elif action == "select":
        project.study_config(study_id)
        project.active_study_id = study_id
    elif action == "delete":
        if study_id == "default":
            raise ValueError("The default study cannot be deleted")
        project.study_config(study_id)
        if any(r.study_id == study_id for r in project.requirements):
            raise ValueError("This study has requirements; reassign them before deletion")
        del project.named_studies[study_id]
        if project.active_study_id == study_id:
            project.active_study_id = "default"
    else:
        raise ValueError("Unknown study operation")
    return store.apply(Command(expected_revision=expected_revision, actor=actor, project=project,
                               summary=f"{action.capitalize()} study {study_id}"))
