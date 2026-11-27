from pathlib import Path

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.branches import branch_map
from release_impact_brief.pipeline import analyze
from release_impact_brief.system import classify, is_test

FASTAPI = {
    "backend/pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", '
    '"sqlmodel", "psycopg[binary]>=3"]\n',
    "backend/app/__init__.py": "",
    "backend/app/core/config.py": """\
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"

settings = Settings()
""",
    "backend/app/models.py": """\
from sqlmodel import Field, Relationship, SQLModel

class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)

class ItemCreate(ItemBase):
    pass

class Item(ItemBase, table=True):
    id: int = Field(primary_key=True)
    owner_id: int
    owner: "User | None" = Relationship(back_populates="items")

class User(SQLModel, table=True):
    __tablename__ = "account"
    id: int = Field(primary_key=True)
    items: list[Item] = Relationship(back_populates="owner")
""",
    "backend/app/crud.py": """\
from app.models import Item, ItemCreate

def create_item(*, session, item_in: ItemCreate) -> Item:
    item = Item.model_validate(item_in)
    session.add(item)
    return item

def count(session) -> int:
    return len(session.exec().all())
""",
    "backend/app/api/deps.py": """\
from typing import Annotated
from fastapi import Depends
from sqlmodel import Session

def get_db():
    yield Session()

SessionDep = Annotated[Session, Depends(get_db)]
""",
    "backend/app/api/routes/items.py": """\
from fastapi import APIRouter, HTTPException, Query
from app import crud
from app.api.deps import SessionDep
from app.models import ItemCreate

router = APIRouter(prefix="/items")

@router.get("/{id}")
def read_item(session: SessionDep, id: int, verbose: bool = Query(False)):
    item = session.get(id)
    if not item:
        raise HTTPException(status_code=404)
    return item

@router.post("/")
def create_item(session: SessionDep, item_in: ItemCreate):
    if item_in.title == "":
        raise HTTPException(status_code=400)
    return crud.create_item(session=session, item_in=item_in)
""",
    "backend/app/api/main.py": """\
from fastapi import APIRouter
from app.api.routes import items

api_router = APIRouter()
api_router.include_router(items.router)
""",
    "backend/app/main.py": """\
from fastapi import FastAPI
from app.api.main import api_router
from app.core.config import settings

app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)
""",
    "backend/tests/test_items.py": """\
from app import crud

def test_create():
    crud.create_item(session=None, item_in=None)
""",
}

FLASK = {
    "requirements.txt": "Flask==3.0\nFlask-SQLAlchemy==3.1\nredis==5.0  # cache\n",
    "app/__init__.py": """\
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    app = Flask(__name__)
    from app.main import bp as main_bp
    app.register_blueprint(main_bp, url_prefix="/web")
    return app
""",
    "app/models.py": """\
import sqlalchemy.orm as so
from app import db

class Mixin:
    def to_dict(self):
        return {}

class BlogPost(Mixin, db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    author: so.Mapped["Writer"] = so.relationship(back_populates="posts")

class Writer(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    posts: so.WriteOnlyMapped["BlogPost"] = so.relationship(back_populates="author")

    def describe(self):
        return self.to_label()

    def to_label(self):
        return "w"
""",
    "app/forms.py": """\
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Length

class PostForm(FlaskForm):
    body = StringField("Say", validators=[DataRequired(), Length(max=140)])
""",
    "app/main/__init__.py": """\
from flask import Blueprint

bp = Blueprint("main", __name__)

from app.main import routes
""",
    "app/main/routes.py": """\
from flask import render_template, request
from app import db
from app.forms import PostForm
from app.main import bp
from app.models import BlogPost

@bp.route("/posts/<int:id>", methods=["GET", "POST"])
def post(id):
    form = PostForm()
    if form.validate_on_submit():
        db.session.add(BlogPost(body=form.body.data))
    page = request.args.get("page", 1, type=int)
    if page > 3:
        return render_template("old.html")
    return render_template("post.html", form=form)
""",
}


def build(root: Path, files: dict[str, str]) -> Extraction:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


@pytest.fixture(scope="module")
def fastapi(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    return build(tmp_path_factory.mktemp("fastapi"), FASTAPI)


@pytest.fixture(scope="module")
def flask(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    return build(tmp_path_factory.mktemp("flask"), FLASK)


def node(facts: Extraction, node_id: str) -> dict[str, object]:
    return next(n for n in facts.nodes if n.id == node_id).attributes


def edge(facts: Extraction, kind: str, src: str, dst: str) -> str | None:
    found = [e.confidence.value for e in facts.edges if (e.kind, e.src, e.dst) == (kind, src, dst)]
    return found[0] if found else None


M = "python:method:backend.app."


def test_modules_classes_and_functions_get_readable_ids(fastapi: Extraction) -> None:
    ids = {n.id for n in fastapi.nodes}
    assert "python:class:backend.app.models" in ids  # a module is a class-like unit
    assert "python:class:backend.app.models.Item" in ids
    assert f"{M}crud#create_item(session,item_in)" in ids
    assert "python:field:backend.app.core.config.Settings#API_V1_STR" in ids
    assert node(fastapi, "python:class:backend.app.models.Item")["extends"] == ["ItemBase"]


def test_calls_resolve_through_imports(fastapi: Extraction) -> None:
    handler = f"{M}api.routes.items#create_item(session,item_in)"
    assert edge(fastapi, "calls", handler, f"{M}crud#create_item(session,item_in)") == "exact"
    read = f"{M}api.routes.items#read_item(session,id,verbose)"
    # library calls are listed by what they resolve to (SessionDep -> Session), never traced
    assert node(fastapi, read)["library_calls"] == ["sqlmodel.Session.get", "fastapi.HTTPException"]
    # FastAPI calls a dependency for the function that asks for it
    assert edge(fastapi, "calls", read, f"{M}api.deps#get_db()") == "exact"


def test_router_prefixes_are_joined_and_settings_defaults_are_likely(fastapi: Extraction) -> None:
    endpoints = {n.name: n for n in fastapi.nodes if n.kind == "endpoint"}
    assert set(endpoints) == {"GET /api/v1/items/{id}", "POST /api/v1/items"}
    read = endpoints["GET /api/v1/items/{id}"]
    # the prefix comes from a settings default the environment can override
    assert edge(fastapi, "handles", read.id, read.attributes["handler"]) == "likely"
    inputs = {i["name"]: i for i in read.attributes["inputs"]}
    assert set(inputs) == {"id", "verbose"}  # the session is supplied by Depends
    assert inputs["id"]["source"] == "path" and inputs["verbose"]["source"] == "query"
    assert inputs["verbose"]["default"] == "False"
    body = endpoints["POST /api/v1/items"].attributes["inputs"]
    assert body == [{"name": "item_in", "type": "ItemCreate", "param": "item_in",
                     "source": "body", "validated": True, "required": True}]  # fmt: skip


def test_sqlmodel_tables_and_relations(fastapi: Extraction) -> None:
    entities = {n.id: n.name for n in fastapi.nodes if n.kind == "entity"}
    assert entities == {"python:entity:backend.app.models.Item": "item",
                        "python:entity:backend.app.models.User": "account"}  # fmt: skip
    assert edge(fastapi, "relates", "python:field:backend.app.models.Item#owner",
                "python:entity:backend.app.models.User") == "exact"  # fmt: skip


def test_service_module_store_and_tests(fastapi: Extraction) -> None:
    services = [n for n in fastapi.nodes if n.kind == "service"]
    assert [(s.name, s.attributes["module"]) for s in services] == [
        ("shop", "build:module:backend")
    ]
    assert any(n.id == "datastore:postgresql" for n in fastapi.nodes)
    assert classify(fastapi.nodes, fastapi.edges)["runtime"]["evidence"][0]["text"].startswith(
        "FastAPI app shop"
    )
    assert is_test("backend/tests/test_items.py") and is_test("x/conftest.py")
    assert not is_test("backend/app/testing_utils.py")


def test_python_branch_map(fastapi: Extraction) -> None:
    bm = branch_map(fastapi.nodes, fastapi.edges, "python:endpoint:POST /api/v1/items")
    assert [(b["line"], b["drivers"]) for b in bm["branches"]] == [(17, ["item_in.title"])]
    roles = {i["name"]: i["role"] for i in bm["inputs"]}
    assert roles == {"item_in": "drives", "item_in.title": "drives"}
    assert bm["validation"] == {"item_in": ["title"]}  # Field(min_length=..., max_length=...)


def test_flask_blueprints_request_inputs_and_forms(flask: Extraction) -> None:
    endpoints = {n.name: n for n in flask.nodes if n.kind == "endpoint"}
    assert set(endpoints) == {"GET /web/posts/<int:id>", "POST /web/posts/<int:id>"}
    get = endpoints["GET /web/posts/<int:id>"]
    assert get.attributes["response"] == "view"  # render_template
    inputs = {i["name"]: i["source"] for i in get.attributes["inputs"]}
    assert inputs == {"id": "path", "page": "query", "form": "form"}
    bm = branch_map(flask.nodes, flask.edges, get.id)
    drivers = {b["line"]: b["drivers"] for b in bm["branches"]}
    assert drivers == {10: ["validation:form"], 13: ["page"]}
    assert bm["validation"] == {"form": ["body"]}
    body = next(i for i in bm["inputs"] if i["name"] == "form.body")
    assert body["constraints"] == ["DataRequired", "Length"] and body["role"] == "drives"


def test_sqlalchemy_models_and_calls(flask: Extraction) -> None:
    entities = {n.id: n.name for n in flask.nodes if n.kind == "entity"}
    # Flask-SQLAlchemy's default table name; a mixin is not a table
    assert entities == {"python:entity:app.models.BlogPost": "blog_post",
                        "python:entity:app.models.Writer": "writer"}  # fmt: skip
    assert edge(flask, "relates", "python:field:app.models.Writer#posts",
                "python:entity:app.models.BlogPost") == "exact"  # fmt: skip
    describe = "python:method:app.models.Writer#describe()"
    assert edge(flask, "calls", describe, "python:method:app.models.Writer#to_label()") == "exact"
    stores = {n.id for n in flask.nodes if n.kind == "data_store"}
    assert stores == {"datastore:redis"}  # from requirements.txt


def test_unknown_receivers_are_guessed_only_when_one_method_matches(tmp_path: Path) -> None:
    facts = build(tmp_path, {
        "a.py": "class A:\n    def ping(self):\n        pass\n",
        "b.py": "def run(x):\n    x.ping()\n    x.pong()\n    getattr(x, 'ping')()\n",
    })  # fmt: skip
    run = "python:method:b#run(x)"
    assert edge(facts, "calls", run, "python:method:a.A#ping()") == "guess"
    assert node(facts, run)["unresolved_calls"] == ["x.pong (line 3)"]  # nothing to guess from


def test_calls_through_library_objects_are_named_in_the_brief(tmp_path: Path) -> None:
    from release_impact_brief.impact import analyze_impact

    facts = build(tmp_path, {
        "models.py": "class User:\n    def follow(self, other):\n        pass\n",
        "routes.py": "from flask_login import current_user\n\ndef follow(name):\n"
                     "    current_user.follow(name)\n",
    })  # fmt: skip
    brief = analyze_impact(
        facts,
        {"sections": []},
        ["python:method:models.User#follow(other)", "python:method:routes#follow(name)"],
    )
    notes = [n for n in brief["couldnt_see"] if "through library objects" in n]
    # once, and only for the class method: a plain function can't be reached as obj.follow()
    assert len(notes) == 1 and "flask_login.current_user.follow in routes#follow(name)" in notes[0]


def test_locals_made_from_each_other_dont_loop(tmp_path: Path) -> None:
    facts = build(tmp_path, {  # a = b(); b = a() sent name lookup round in a circle
        "a.py": "def run():\n    a = b()\n    b = a()\n    a.go()\n",
    })  # fmt: skip
    assert node(facts, "python:method:a#run()")["unresolved_calls"]


def test_modules_re_exporting_from_each_other_dont_loop(tmp_path: Path) -> None:
    facts = build(tmp_path, {
        "a.py": "from b import X\n",
        "b.py": "from a import X\n",
        "c.py": "import a\n\ndef run():\n    a.X()\n",
    })  # fmt: skip
    assert node(facts, "python:method:c#run()")


def test_python_tests_are_left_out_of_the_bug_shortlist(tmp_path: Path) -> None:
    from release_impact_brief.chat import rank_facts

    facts = build(tmp_path, {
        "app/rules.py": "def match_branch(command):\n    pass\n",
        "tests/test_rules.py": "def test_match_branch():\n    pass\n",
    })  # fmt: skip
    view = {"panels": {"modules": []}, "sections": []}
    ranked = [i for i, _ in rank_facts(facts, view, "match branch fails")]
    assert "python:method:app.rules#match_branch(command)" in ranked
    assert not [i for i in ranked if "test_rules" in i]
