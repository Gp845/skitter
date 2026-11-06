"""Bake a light copy of the robot for the landing page: python scripts/bake_web_robot.py [faces per mesh, default 1000].
The sim keeps the full CAD meshes (42 MB); the browser only needs the silhouette. Needs: pip install trimesh fast-simplification."""
import pathlib
import shutil
import sys

import fast_simplification
import trimesh

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "cortex" / "adapters" / "hexapod_pybullet" / "assembly_05_description"
OUT = ROOT / "web" / "pkg_lite" / "assembly_05_description"
FACES = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

shutil.rmtree(OUT, ignore_errors=True)
(OUT / "meshes").mkdir(parents=True)
shutil.copytree(SRC / "urdf", OUT / "urdf")
before = after = 0
for f in sorted((SRC / "meshes").glob("*.stl")):
    m = trimesh.load_mesh(f)
    if len(m.faces) > FACES:
        v, fc = fast_simplification.simplify(m.vertices, m.faces, target_reduction=1 - FACES / len(m.faces))
        m = trimesh.Trimesh(v, fc)
    m.export(OUT / "meshes" / f.name)
    before, after = before + f.stat().st_size, after + (OUT / "meshes" / f.name).stat().st_size
print(f"{before / 1e6:.1f} MB -> {after / 1e6:.1f} MB in {OUT.relative_to(ROOT)}")
