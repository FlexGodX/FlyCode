"""Explicit local folder registrations, independent of version control."""

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FolderPickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["existing", "create"] = "existing"
    name: str = Field(default="", max_length=100)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)
    create: bool = False

    @field_validator("name", "path")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите название и путь к папке")
        return value


class LocalProject(BaseModel):
    id: str
    name: str
    description: str


class ProjectRegistry(BaseModel):
    projects: list[LocalProject] = Field(default_factory=list)

    def register(self, request: ProjectRequest, workspace: Path, storage: Path) -> LocalProject:
        target = Path(request.path).expanduser()
        if not target.is_absolute():
            raise ValueError("Укажите полный путь к локальной папке")
        target = target.resolve()
        if request.create:
            target.mkdir()
        if not target.is_dir():
            raise ValueError("Папка не найдена. Выберите существующую папку")
        for project in self.projects:
            if project.description == str(target):
                return project
        project_id = "local:" + uuid4().hex
        if target.is_relative_to(workspace):
            relative = target.relative_to(workspace)
            if not any(part.startswith(".") for part in relative.parts):
                project_id = "." if target == workspace else f"./{relative}"
        project = LocalProject(id=project_id, name=request.name, description=str(target))
        updated = self.model_copy(update={"projects": [*self.projects, project]})
        temporary = storage.with_suffix(".tmp")
        temporary.write_text(updated.model_dump_json(), encoding="utf-8")
        temporary.replace(storage)
        self.projects.append(project)
        return project

    def resolve(self, project_id: str) -> Path | None:
        project = next((item for item in self.projects if item.id == project_id), None)
        if project is None:
            return None
        root = Path(project.description)
        if root.resolve() != root or not root.is_dir():
            raise ValueError("Папка проекта недоступна или перемещена")
        return root
