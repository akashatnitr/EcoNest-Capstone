"""Parse RDF/Turtle and map ontology classes to ArcadeDB vertex types."""

from pathlib import Path
from typing import Optional

from rdflib import OWL, RDF, RDFS, Graph, Namespace

from orchestrator.core.database import arcadedb_query

ECONEST = Namespace("http://econest.example.org/ontology#")


async def load_ontology(turtle_path: Optional[str] = None) -> dict:
    """Parse a Turtle file and sync its ontology into ArcadeDB.

    Returns a summary of created classes and properties.
    """
    path = turtle_path or str(Path(__file__).parent / "smart_home.ttl")
    g = Graph()
    g.parse(path, format="turtle")

    created_classes = []
    created_edges = []

    # Create Class vertices and SUBCLASS_OF edges
    for cls in g.subjects(RDF.type, OWL.Class):
        local_name = cls.split("#")[-1]
        parent = None
        for p in g.objects(cls, RDFS.subClassOf):
            parent = p.split("#")[-1]

        # Upsert Class vertex
        await arcadedb_query(
            "sql",
            f"CREATE VERTEX Class IF NOT EXISTS SET name = '{local_name}'",
            readonly=False,
        )
        created_classes.append(local_name)

        if parent:
            await arcadedb_query(
                "sql",
                (
                    f"CREATE EDGE SUBCLASS_OF IF NOT EXISTS "
                    f"FROM (SELECT FROM Class WHERE name = '{local_name}') "
                    f"TO (SELECT FROM Class WHERE name = '{parent}')"
                ),
                readonly=False,
            )

    # Map object properties to edge types
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        local_name = prop.split("#")[-1]
        await arcadedb_query(
            "sql",
            f"CREATE EDGE TYPE {local_name} IF NOT EXISTS",
            readonly=False,
        )
        created_edges.append(local_name)

    # Map data properties to vertex properties (document as schema metadata)
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        local_name = prop.split("#")[-1]
        # Store as metadata on a Property vertex
        await arcadedb_query(
            "sql",
            f"CREATE VERTEX Property IF NOT EXISTS SET name = '{local_name}'",
            readonly=False,
        )

    return {
        "classes": created_classes,
        "edges": created_edges,
        "file": path,
    }
