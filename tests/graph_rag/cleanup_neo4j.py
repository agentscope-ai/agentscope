# -*- coding: utf-8 -*-
"""Clean up Neo4j database before running tests."""

import asyncio
import os
import sys
from pathlib import Path

from neo4j import AsyncGraphDatabase

# Add src to path if needed for development
src_path = Path(__file__).parent.parent.parent / "src"
if src_path.exists() and str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


async def cleanup_neo4j() -> None:
    """Clean up all data and indexes from Neo4j."""
    print("=" * 80)
    print("Neo4j Cleanup Script")
    print("=" * 80)
    print("\n📌 Connecting to Neo4j:")
    print(f"   URI: {NEO4J_URI}")
    print(f"   Database: {NEO4J_DATABASE}")
    print(f"   User: {NEO4J_USER}")

    driver = AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )

    try:
        async with driver.session(database=NEO4J_DATABASE) as session:
            # Show current indexes
            print("\n📋 Current indexes:")
            result = await session.run("SHOW INDEXES")
            indexes = await result.data()

            if not indexes:
                print("   No indexes found")
            else:
                for idx in indexes:
                    print(f"   - {idx.get('name')} ({idx.get('type')})")

            # Count nodes
            result = await session.run("MATCH (n) RETURN count(n) as count")
            record = await result.single()
            node_count = record["count"] if record else 0
            print(f"\n📊 Current node count: {node_count}")

            # Ask for confirmation (unless --yes flag is provided)
            if node_count > 0 or indexes:
                if "--yes" not in sys.argv:
                    print(
                        "\n⚠️  WARNING: This will delete ALL data and indexes!",
                    )
                    response = input("   Continue? (yes/no): ")
                    if response.lower() != "yes":
                        print("\n❌ Cleanup cancelled")
                        return
                else:
                    print(
                        "\n⚠️  Auto-confirming cleanup (--yes flag provided)",
                    )

            # Delete all nodes and relationships
            if node_count > 0:
                print("\n🗑️  Deleting all nodes and relationships...")
                await session.run("MATCH (n) DETACH DELETE n")
                print("   ✅ Deleted all nodes and relationships")

            # Drop all indexes
            if indexes:
                print("\n🗑️  Dropping indexes...")
                for index in indexes:
                    index_name = index.get("name")
                    if index_name:
                        try:
                            await session.run(
                                f"DROP INDEX `{index_name}` IF EXISTS",
                            )
                            print(f"   ✅ Dropped index: {index_name}")
                        except Exception as e:
                            print(
                                f"   ⚠️  Failed to drop index {index_name}: {e}",
                            )

            # Verify cleanup
            result = await session.run("MATCH (n) RETURN count(n) as count")
            record = await result.single()
            final_count = record["count"] if record else 0

            result = await session.run("SHOW INDEXES")
            final_indexes = await result.data()

            print("\n" + "=" * 80)
            print("Cleanup Summary")
            print("=" * 80)
            print(f"Nodes remaining: {final_count}")
            print(f"Indexes remaining: {len(final_indexes)}")

            if final_count == 0 and len(final_indexes) == 0:
                print("\n✅ Neo4j cleanup completed successfully!")
                print("\nYou can now run the compatibility tests:")
                print("   python test_model_compatibility.py")
            else:
                print("\n⚠️  Some data or indexes may still remain")
                if final_indexes:
                    print("\nRemaining indexes:")
                    for idx in final_indexes:
                        print(f"   - {idx.get('name')} ({idx.get('type')})")

    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(cleanup_neo4j())
