"""Ontology API routes."""

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from rdflib import OWL, RDF, RDFS, Graph, Literal, URIRef

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import USER_ADMIN, has_permission
from orchestrator.ontology.loader import DEFAULT_ONTOLOGY_PATH, load_ontology
from orchestrator.ontology.reasoner import run_reasoner
from orchestrator.ontology.validator import validate_graph

router = APIRouter(prefix="/ontology", tags=["ontology"])


class OntologyClassSummary(BaseModel):
    """Compact class metadata for the ontology listing."""

    name: str
    uri: str
    label: str | None = None
    comment: str | None = None
    superclasses: list[str] = Field(default_factory=list)


class OntologyPropertySummary(BaseModel):
    """Compact object or datatype property metadata."""

    name: str
    uri: str
    label: str | None = None
    comment: str | None = None
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)


class OntologyRestriction(BaseModel):
    """OWL restriction details exposed by the class endpoint."""

    property: str
    restriction_type: str
    value: str | int | None = None
    target_class: str | None = None


class OntologyClassDetail(OntologyClassSummary):
    """Detailed class metadata including restrictions and local properties."""

    restrictions: list[OntologyRestriction] = Field(default_factory=list)
    object_properties: list[str] = Field(default_factory=list)
    data_properties: list[str] = Field(default_factory=list)
    inferred_capabilities: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)


class OntologySummary(BaseModel):
    """Ontology listing response."""

    classes: list[str]
    object_properties: list[str]
    data_properties: list[str]
    class_details: list[OntologyClassSummary]
    object_property_details: list[OntologyPropertySummary]
    data_property_details: list[OntologyPropertySummary]


@router.get("", response_model=OntologySummary)
async def list_ontology(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> OntologySummary:
    """List ontology classes and properties from the Turtle ontology."""
    del current_user

    graph = _load_default_graph()
    classes = _class_summaries(graph)
    object_properties = _property_summaries(graph, OWL.ObjectProperty)
    data_properties = _property_summaries(graph, OWL.DatatypeProperty)

    return OntologySummary(
        classes=[item.name for item in classes],
        object_properties=[item.name for item in object_properties],
        data_properties=[item.name for item in data_properties],
        class_details=classes,
        object_property_details=object_properties,
        data_property_details=data_properties,
    )


@router.get("/classes/{name}", response_model=OntologyClassDetail)
async def get_class(
    name: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> OntologyClassDetail:
    """Return class details and OWL restrictions from the Turtle ontology."""
    del current_user

    graph = _load_default_graph()
    class_uri = _find_named_subject(graph, OWL.Class, name)
    if class_uri is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Class not found"
        )

    restrictions = _class_restrictions(graph, class_uri)
    object_properties = _properties_for_class(graph, OWL.ObjectProperty, class_uri)
    data_properties = _properties_for_class(graph, OWL.DatatypeProperty, class_uri)
    capability_restrictions = [
        restriction.value
        for restriction in restrictions
        if restriction.property == "hasCapability"
        and restriction.restriction_type == "someValuesFrom"
        and isinstance(restriction.value, str)
    ]
    required_capabilities = [
        restriction.value
        for restriction in restrictions
        if restriction.property == "requiresCapability"
        and restriction.restriction_type == "someValuesFrom"
        and isinstance(restriction.value, str)
    ]

    return OntologyClassDetail(
        name=name,
        uri=str(class_uri),
        label=_literal_value(graph, class_uri, RDFS.label),
        comment=_literal_value(graph, class_uri, RDFS.comment),
        superclasses=_named_superclasses(graph, class_uri),
        restrictions=restrictions,
        object_properties=object_properties,
        data_properties=data_properties,
        inferred_capabilities=capability_restrictions,
        required_capabilities=required_capabilities,
    )


@router.get("/validate")
async def validate(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run validation on current graph."""
    del current_user
    return await validate_graph()


@router.post("/reason")
async def reason(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run reasoner and return inferred triples."""
    del current_user
    return await run_reasoner()


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_ontology(
    file: UploadFile,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Upload and sync a Turtle ontology file (admin only)."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    if not file.filename or Path(file.filename).suffix.lower() != ".ttl":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .ttl files are accepted",
        )

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    try:
        result = await load_ontology(str(temp_path))
    finally:
        temp_path.unlink(missing_ok=True)

    return {"message": "Ontology uploaded", "summary": result}


def _load_default_graph() -> Graph:
    graph = Graph()
    graph.parse(DEFAULT_ONTOLOGY_PATH, format="turtle")
    return graph


def _class_summaries(graph: Graph) -> list[OntologyClassSummary]:
    summaries = [
        OntologyClassSummary(
            name=_local_name(subject),
            uri=str(subject),
            label=_literal_value(graph, subject, RDFS.label),
            comment=_literal_value(graph, subject, RDFS.comment),
            superclasses=_named_superclasses(graph, subject),
        )
        for subject in graph.subjects(RDF.type, OWL.Class)
        if isinstance(subject, URIRef)
    ]
    return sorted(summaries, key=lambda item: item.name)


def _property_summaries(
    graph: Graph,
    property_type: URIRef,
) -> list[OntologyPropertySummary]:
    summaries = [
        OntologyPropertySummary(
            name=_local_name(subject),
            uri=str(subject),
            label=_literal_value(graph, subject, RDFS.label),
            comment=_literal_value(graph, subject, RDFS.comment),
            domain=_named_values(graph.objects(subject, RDFS.domain)),
            range=_named_values(graph.objects(subject, RDFS.range)),
        )
        for subject in graph.subjects(RDF.type, property_type)
        if isinstance(subject, URIRef)
    ]
    return sorted(summaries, key=lambda item: item.name)


def _find_named_subject(
    graph: Graph,
    rdf_type: URIRef,
    name: str,
) -> URIRef | None:
    for subject in graph.subjects(RDF.type, rdf_type):
        if isinstance(subject, URIRef) and _local_name(subject) == name:
            return subject
    return None


def _class_restrictions(graph: Graph, class_uri: URIRef) -> list[OntologyRestriction]:
    restrictions: list[OntologyRestriction] = []
    for subclass in graph.objects(class_uri, RDFS.subClassOf):
        if (subclass, RDF.type, OWL.Restriction) not in graph:
            continue
        property_uri = graph.value(subclass, OWL.onProperty)
        property_name = _node_name(property_uri)
        if property_name is None:
            continue

        some_values_from = graph.value(subclass, OWL.someValuesFrom)
        qualified_cardinality = graph.value(subclass, OWL.qualifiedCardinality)
        on_class = graph.value(subclass, OWL.onClass)

        if some_values_from is not None:
            restrictions.append(
                OntologyRestriction(
                    property=property_name,
                    restriction_type="someValuesFrom",
                    value=_node_name(some_values_from),
                )
            )
        if qualified_cardinality is not None:
            restrictions.append(
                OntologyRestriction(
                    property=property_name,
                    restriction_type="qualifiedCardinality",
                    value=_literal_python_value(qualified_cardinality),
                    target_class=_node_name(on_class),
                )
            )

    return sorted(
        restrictions,
        key=lambda item: (item.property, item.restriction_type, str(item.value)),
    )


def _properties_for_class(
    graph: Graph,
    property_type: URIRef,
    class_uri: URIRef,
) -> list[str]:
    class_names = {_local_name(class_uri), *_named_superclasses(graph, class_uri)}
    properties: list[str] = []
    for subject in graph.subjects(RDF.type, property_type):
        if not isinstance(subject, URIRef):
            continue
        domains = _named_values(graph.objects(subject, RDFS.domain))
        if "Thing" in domains or any(domain in class_names for domain in domains):
            properties.append(_local_name(subject))
    return sorted(set(properties))


def _named_superclasses(graph: Graph, class_uri: URIRef) -> list[str]:
    return _named_values(
        item
        for item in graph.objects(class_uri, RDFS.subClassOf)
        if isinstance(item, URIRef)
    )


def _named_values(values: Any) -> list[str]:
    names = [_node_name(value) for value in values]
    return sorted({name for name in names if name is not None})


def _literal_value(graph: Graph, subject: URIRef, predicate: URIRef) -> str | None:
    value = graph.value(subject, predicate)
    if isinstance(value, Literal):
        return str(value)
    return None


def _literal_python_value(value: Any) -> str | int | None:
    if isinstance(value, Literal):
        python_value = value.toPython()
        if isinstance(python_value, int):
            return python_value
        return str(python_value)
    return _node_name(value)


def _node_name(value: Any) -> str | None:
    if isinstance(value, URIRef):
        return _local_name(value)
    if isinstance(value, Literal):
        return str(value)
    return None


def _local_name(uri: URIRef) -> str:
    value = str(uri)
    if "#" in value:
        return value.rsplit("#", maxsplit=1)[-1]
    return value.rstrip("/").rsplit("/", maxsplit=1)[-1]
