from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ApiEvidence(BaseModel):
    method: str
    path: str
    controller: str = ""
    module: str = ""
    router_file: str = ""
    source_file: str = ""
    line: int = 0
    middleware: List[str] = Field(default_factory=list)
    auth_required: Optional[bool] = None

class RouteEvidence(BaseModel):
    name: str
    type: str
    source_file: str = ""
    routes: List[str] = Field(default_factory=list)

class DependencyEvidence(BaseModel):
    name: str
    module: str = ""
    file: str = ""
    type: Optional[str] = None
    functions: List[str] = Field(default_factory=list)
    entity: str = ""

class SymbolEvidence(BaseModel):
    name: str
    type: str
    database: str = ""
    orm: str = ""
    source_file: str = ""
    fields: Dict[str, Any] = Field(default_factory=dict)

class ModuleEvidence(BaseModel):
    name: str
    path: str = ""
    type: str
    framework: Optional[str] = None
    files: List[str] = Field(default_factory=list)

class MountEvidence(BaseModel):
    mount_path: str = ""
    mounted_router: str = ""
    parent: str = ""
    router: str = ""
    router_file: str = ""
    path: str = ""
    source_file: str = ""
    line: int = 0

class RelationshipEvidence(BaseModel):
    type: str
    from_: str = Field(alias="from")
    to: str
    relation: Optional[str] = None
    field: Optional[str] = None

    class Config:
        populate_by_name = True

class FrontendRouteEvidence(BaseModel):
    path: str
    component: str
    source_file: str = ""
    framework: str = ""


class RepositoryEvidence(BaseModel):
    tech_stack: Dict[str, List[str]]
    modules: List[ModuleEvidence]
    apis: List[ApiEvidence]
    entities: List[SymbolEvidence]
    services: List[DependencyEvidence]
    repositories: List[DependencyEvidence]
    mounts: List[MountEvidence]
    relationships: List[RelationshipEvidence]
    frontend_routes: List[FrontendRouteEvidence]
    components: List[ModuleEvidence]
    routers: List[RouteEvidence]
