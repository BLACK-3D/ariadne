"""Finite offline production tools, with lazy native engines and immutable inputs.

No argument becomes source code, a shell command, a URL, or an executable path.
These workers have resource bounds; they are not an OS security sandbox.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import re
import secrets
import stat
import tempfile
import threading
from pathlib import Path
from decimal import Decimal, localcontext

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "runtime/production/python"
MANIFEST = ROOT / "runtime/production/installed.json"
PINNED = {"ortools": "9.15.6755", "sympy": "1.14.0"}
MEMORY_BYTES = 2 * 1024**3
CPU_SECONDS = 45
WALL_SECONDS = 90
MAX_INPUT = 20_000_000
MAX_OUTPUT = 20_000_000
MAX_REQUEST = 28_000_000
NATIVE = {"libreoffice", "freecad", "blender", "kicad", "qgis", "ffmpeg"}
NAMES = {"production_optimize", "production_math", "office_create", "geo_analyze",
         "cad_part", "render_product", "pcb_manufacture", "gis_buffer", "media_transform"}


def obj(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Missing or unsupported production arguments.")
    return value


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer between {low} and {high}.")
    return value


def number(value, low, high, label):
    if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be finite and between {low} and {high}.")
    return value


def text(value, maximum=10000):
    if type(value) is not str or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Text exceeds its length/control-character limit.")
    return value


def _enum(value, allowed):
    if type(value) is not str or value not in allowed:
        raise ValueError("Unsupported fixed operation.")
    return value


def validate_math(args):
    obj(args, {"operation", "expression", "variable", "values"}, {"operation", "expression"})
    operation = _enum(args["operation"], {"simplify", "expand", "factor", "differentiate", "integrate", "solve", "evaluate"})
    expression = text(args["expression"], 1000)
    variable = args.get("variable", "x")
    if variable not in {"x", "y", "z", "t"}:
        raise ValueError("Variables are limited to x, y, z and t.")
    values = obj(args.get("values", {}), {"x", "y", "z", "t"})
    for value in values.values():
        number(value, -1e9, 1e9, "substitution")
    # The worker constructs an expression from this AST; it never calls eval,
    # sympify on text, parse_expr, lambdify, or a user-provided callable.
    import ast
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 160:
        raise ValueError("Expression is too complex.")
    allowed_calls = {"sin", "cos", "tan", "exp", "log", "sqrt", "abs"}
    def visit(node, depth=0):
        if depth > 16:
            raise ValueError("Expression nesting exceeds its limit.")
        if isinstance(node, ast.Constant):
            number(node.value, -1e9, 1e9, "constant")
        elif isinstance(node, ast.Name):
            if node.id not in {"x", "y", "z", "t", "pi", "E"}:
                raise ValueError("Unknown mathematical symbol.")
        elif isinstance(node, ast.BinOp) and type(node.op) in {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow}:
            visit(node.left, depth + 1)
            visit(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                exponent = node.right
                if isinstance(exponent, ast.UnaryOp) and isinstance(exponent.op, ast.USub):
                    exponent = exponent.operand
                if not isinstance(exponent, ast.Constant) or type(exponent.value) is not int or abs(exponent.value) > 12:
                    raise ValueError("Powers require a literal integer exponent from -12 to 12.")
        elif isinstance(node, ast.UnaryOp) and type(node.op) in {ast.UAdd, ast.USub}:
            visit(node.operand, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in allowed_calls and len(node.args) == 1 and not node.keywords:
            visit(node.args[0], depth + 1)
        else:
            raise ValueError("Only bounded mathematical operators, symbols and approved single-argument functions are accepted.")
    visit(tree.body)
    return {"operation": operation, "expression": expression, "variable": variable, "values": values}


def validate_optimization(args):
    obj(args, {"operation", "costs", "durations", "precedence", "machines", "weights", "capacity", "distance_matrix", "vehicles", "depot", "seconds"}, {"operation"})
    kind = _enum(args["operation"], {"assignment", "schedule", "packing", "routing"})
    result = {"operation": kind, "seconds": integer(args.get("seconds", 5), 1, 15, "solver seconds")}
    fields = {"assignment": {"costs"}, "schedule": {"durations", "precedence", "machines"},
              "packing": {"weights", "capacity"}, "routing": {"distance_matrix", "vehicles", "depot"}}[kind]
    if set(args) - fields - {"operation", "seconds"}:
        raise ValueError("Arguments belong to another optimization template.")
    if kind in {"assignment", "routing"}:
        key = "costs" if kind == "assignment" else "distance_matrix"
        matrix = args.get(key)
        cap = 40 if kind == "assignment" else 50
        if type(matrix) is not list or not 1 <= len(matrix) <= cap or any(type(r) is not list for r in matrix):
            raise ValueError("Invalid bounded integer cost matrix.")
        width = len(matrix[0])
        if not 1 <= width <= cap or any(len(row) != width for row in matrix):
            raise ValueError("Cost matrix must be rectangular.")
        result[key] = [[integer(v, 0, 1_000_000, "cost/distance") for v in row] for row in matrix]
        if kind == "assignment" and width > len(matrix):
            raise ValueError("Assignment needs at least as many workers (rows) as tasks (columns).")
        if kind == "routing":
            if width != len(matrix) or width < 2 or any(matrix[i][i] != 0 for i in range(width)):
                raise ValueError("Routing requires a square distance matrix with a zero diagonal and at least two locations.")
            result["vehicles"] = integer(args.get("vehicles", 1), 1, min(8, width - 1), "vehicles")
            result["depot"] = integer(args.get("depot", 0), 0, width - 1, "depot")
    elif kind == "packing":
        weights = args.get("weights")
        if type(weights) is not list or not 1 <= len(weights) <= 80:
            raise ValueError("Packing needs 1–80 positive integer weights.")
        result["capacity"] = integer(args.get("capacity"), 1, 1_000_000, "capacity")
        result["weights"] = [integer(v, 1, result["capacity"], "item weight") for v in weights]
    else:
        durations = args.get("durations")
        if type(durations) is not list or not 1 <= len(durations) <= 80:
            raise ValueError("Scheduling needs 1–80 positive integer durations.")
        result["durations"] = [integer(v, 1, 100000, "duration") for v in durations]
        machines = args.get("machines", [0] * len(durations))
        if type(machines) is not list or len(machines) != len(durations):
            raise ValueError("Each task needs a machine index.")
        result["machines"] = [integer(v, 0, 15, "machine") for v in machines]
        pairs = args.get("precedence", [])
        if type(pairs) is not list or len(pairs) > 200:
            raise ValueError("At most 200 precedence pairs are accepted.")
        result["precedence"] = []
        for pair in pairs:
            if type(pair) is not list or len(pair) != 2:
                raise ValueError("Precedence pairs must be [before, after] task indices.")
            clean = [integer(v, 0, len(durations)-1, "task index") for v in pair]
            if clean[0] == clean[1]:
                raise ValueError("A task cannot precede itself.")
            result["precedence"].append(clean)
    return result


def validate_office(args):
    obj(args, {"format", "path", "title", "paragraphs", "rows", "slides"}, {"format", "path", "title"})
    fmt = _enum(args["format"], {"docx", "xlsx", "pptx", "pdf"})
    result = {"format": fmt, "path": text(args["path"], 1024), "title": text(args["title"], 200)}
    field = "rows" if fmt == "xlsx" else "slides" if fmt == "pptx" else "paragraphs"
    if set(args) - {"format", "path", "title", field}:
        raise ValueError("Content fields must match the chosen office format.")
    items = args.get(field, [])
    if type(items) is not list or not 1 <= len(items) <= (1000 if field == "rows" else 60):
        raise ValueError("Supply a bounded, nonempty content list.")
    if field == "rows":
        if any(type(row) is not list or not 1 <= len(row) <= 30 for row in items) or sum(map(len, items)) > 10000:
            raise ValueError("Spreadsheet supports at most 10,000 cells, 1,000 rows and 30 columns.")
        result[field], _ = spreadsheet_cells(items)
    elif field == "slides":
        result[field] = []
        for slide in items:
            obj(slide, {"title", "body"}, {"title", "body"})
            result[field].append({"title": text(slide["title"], 200), "body": text(slide["body"], 3000)})
    else:
        result[field] = [text(v, 10000) for v in items]
    return result


def spreadsheet_cells(rows):
    """Literal strings, explicit decimal cells and finite local range formulas.

    Fixed SUM/SUMPRODUCT ranges may reference numeric input cells only, never
    another formula, external workbooks, names or implicit/coerced text. Numeric
    output uses Excel's 15 significant digit practical precision contract.
    """
    numbers={}; formulas={}; clean=[]
    def decimal(raw):
        if type(raw) is int: raw=str(integer(raw,-10**12,10**12,'spreadsheet integer'))
        if type(raw) is not str or len(raw)>40 or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',raw):
            raise ValueError('Numeric cells require a plain finite decimal string or bounded integer.')
        value=Decimal(raw)
        normalized=value.normalize()
        if abs(value)>10**15-1 or len(normalized.as_tuple().digits)>15:
            raise ValueError('Spreadsheet numbers and formula totals are limited to 15 significant digits and magnitude below 10^15.')
        return value,'0' if value==0 else format(normalized,'f')
    for r,row in enumerate(rows,1):
        output=[]
        for c,cell in enumerate(row,1):
            if type(cell) is str: output.append(text(cell,2000)); continue
            if type(cell) is int:
                value,raw=decimal(cell); numbers[r,c]=value; output.append({'number':raw}); continue
            obj(cell,{'number','sum','sum_product'})
            if len(cell)!=1: raise ValueError('Each typed spreadsheet cell accepts exactly one fixed operation.')
            if 'number' in cell:
                value,raw=decimal(cell['number']); numbers[r,c]=value; output.append({'number':raw})
            else:
                formulas[r,c]=cell; output.append(cell)
        clean.append(output)
    def cell_ref(value):
        match=re.fullmatch(r'([A-Z]{1,2})([1-9][0-9]{0,3})',value)
        if not match: raise ValueError('Use uppercase local A1-style cell references only.')
        col=0
        for char in match[1]: col=col*26+ord(char)-64
        return integer(int(match[2]),1,1000,'cell row'),integer(col,1,30,'cell column')
    def region(value):
        if type(value) is not str or len(value)>24: raise ValueError('Invalid bounded local cell range.')
        parts=value.split(':')
        if not 1<=len(parts)<=2: raise ValueError('Use one local cell or a rectangular range.')
        first=cell_ref(parts[0]); last=cell_ref(parts[-1])
        if first[0]>last[0] or first[1]>last[1]: raise ValueError('Cell range bounds are reversed.')
        refs=[(r,c) for r in range(first[0],last[0]+1) for c in range(first[1],last[1]+1)]
        if len(refs)>10000 or any(ref not in numbers for ref in refs):
            raise ValueError('Fixed formulas may reference only explicit numeric input cells; formulas, blanks, text, missing and external cells are rejected.')
        return refs,(last[0]-first[0],last[1]-first[1])
    cached={}
    with localcontext() as context:
        context.prec=80
        for key,formula in formulas.items():
            if 'sum' in formula:
                refs,_=region(formula['sum']); value=sum((numbers[k] for k in refs),Decimal(0))
            else:
                ranges=formula['sum_product']
                if type(ranges) is not list or len(ranges)!=2: raise ValueError('sum_product needs two same-shaped local ranges.')
                left,shape=region(ranges[0]); right,other=region(ranges[1])
                if shape!=other: raise ValueError('sum_product ranges must have identical dimensions.')
                value=sum((numbers[a]*numbers[b] for a,b in zip(left,right)),Decimal(0))
            _,cached[key]=decimal(format(value,'f'))
    return clean,cached


def validate_args(name, args):
    if name == "production_math":
        return validate_math(args)
    if name == "production_optimize":
        return validate_optimization(args)
    if name == "office_create":
        return validate_office(args)
    if name == "geo_analyze":
        obj(args, {"path", "operation", "bbox"}, {"path"})
        result = {"path": text(args["path"], 1024), "operation": _enum(args.get("operation", "summary"), {"summary", "filter_bbox"})}
        if result["operation"] == "filter_bbox":
            box = args.get("bbox")
            if type(box) is not list or len(box) != 4:
                raise ValueError("bbox must be [west,south,east,north].")
            result["bbox"] = [number(v, -180 if i % 2 == 0 else -90, 180 if i % 2 == 0 else 90, "bbox coordinate") for i,v in enumerate(box)]
            if box[0] > box[2] or box[1] > box[3]:
                raise ValueError("bbox minimums must precede maximums; antimeridian boxes must be split.")
        elif "bbox" in args:
            raise ValueError("bbox is only accepted with filter_bbox.")
        return result
    if name == "cad_part":
        obj(args, {"shape", "dimensions", "path", "format"}, {"shape", "dimensions", "path"})
        kind = _enum(args["shape"], {"box", "cylinder", "plate_with_holes"})
        dims = obj(args["dimensions"], {"length", "width", "height"} if kind != "cylinder" else {"radius", "height"}, {"radius", "height"} if kind == "cylinder" else {"length", "width", "height"})
        return {"shape": kind, "dimensions": {k: number(v, 1, 1000, k) for k,v in dims.items()}, "path": text(args["path"],1024), "format": _enum(args.get("format", "step"), {"step", "stl"})}
    if name == "render_product":
        obj(args, {"shape", "dimensions", "color", "size", "path"}, {"shape", "path"})
        dims = args.get("dimensions", [2,2,2])
        color = args.get("color", [0.2,0.5,0.25])
        if type(dims) is not list or len(dims) != 3 or type(color) is not list or len(color) != 3:
            raise ValueError("dimensions and color must have three numbers.")
        return {"shape": _enum(args["shape"], {"box", "cylinder", "sphere"}), "dimensions": [number(x,.01,100,"dimension") for x in dims], "color": [number(x,0,1,"color") for x in color], "size": integer(args.get("size",512),128,1024,"render size"), "path":text(args["path"],1024)}
    if name == "pcb_manufacture":
        obj(args, {"width", "height", "path"}, {"width", "height", "path"})
        return {"width":number(args["width"],10,200,"board width"),"height":number(args["height"],10,200,"board height"),"path":text(args["path"],1024)}
    if name == "gis_buffer":
        obj(args, {"path", "output", "distance_degrees"}, {"path", "output", "distance_degrees"})
        return {"path":text(args["path"],1024),"output":text(args["output"],1024),"distance_degrees":number(args["distance_degrees"],.000001,1,"buffer in WGS84 degrees")}
    if name == "media_transform":
        obj(args, {"path", "output", "operation", "start", "duration"}, {"path", "output", "operation"})
        return {"path":text(args["path"],1024),"output":text(args["output"],1024),"operation":_enum(args["operation"],{"audio_wav","thumbnail","clip_mp4"}),"start":number(args.get("start",0),0,3600,"start seconds"),"duration":number(args.get("duration",30),.1,120,"duration seconds")}
    raise ValueError("Unknown production tool.")


def _local(path, limit=500_000_000):
    from .config import _allowed_path
    path = _allowed_path(path)
    path.relative_to(ROOT)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("Production assets must be bounded, unlinked regular files inside this installation.")
    return path


def installation():
    """Inspect only the owner-provisioned manifest; never discover PATH executables."""
    try:
        manifest = json.loads(_local(MANIFEST, 500_000).read_text(encoding="utf-8"))
        if manifest.get("schema") != 1:
            raise ValueError("Unsupported production manifest.")
        asset_lock = _local(ROOT/"config/production-assets.json",100000)
        if manifest.get("wheel_lock_sha256") != hashlib.sha256(asset_lock.read_bytes()).hexdigest():
            raise ValueError("Production package lock changed; run the explicit provisioner again.")
        from .config import _allowed_path
        _allowed_path(VENDOR)
        packages = {}
        for name, version in PINNED.items():
            matching = [d for d in importlib.metadata.distributions(path=[str(VENDOR)]) if d.metadata["Name"].lower() == name]
            if matching and matching[0].version == version and manifest.get("packages",{}).get(name) == version:
                packages[name] = version
        binaries = {}
        for name, item in manifest.get("binaries", {}).items():
            if name not in NATIVE or type(item) is not dict:
                continue
            try:
                path = _local(ROOT / item["path"])
                # Verify the exact native executable, not a same-named PATH program.
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                        continue
                binaries[name] = {"path":str(path), "version":text(item["version"],100), "sha256":item["sha256"]}
            except (OSError,ValueError,KeyError,TypeError):
                continue
        bpy_engine = manifest.get("python_engines",{}).get("blender",{})
        if bpy_engine.get("version") == "5.2.3":
            try:
                lock = _local(ROOT/"runtime/production/blender-wheels-lock.json",100000)
                if hashlib.sha256(lock.read_bytes()).hexdigest() != bpy_engine.get("lock_sha256"):
                    raise ValueError("Blender Python wheel lock changed.")
                vendor = _allowed_path(ROOT/"runtime/production/blender-python")
                versions = {d.metadata["Name"].lower():d.version for d in importlib.metadata.distributions(path=[str(vendor)])}
                if versions.get("bpy") == "5.2.3":
                    binaries["blender"] = {"path":str(vendor),"version":"5.2.3","sha256":bpy_engine["lock_sha256"],"kind":"bpy"}
            except (OSError,ValueError,KeyError,TypeError):
                pass
        return {"packages":packages,"binaries":binaries}
    except (OSError, ValueError, KeyError, TypeError):
        return {"packages":{},"binaries":{}}


class ProductionTools:
    def __init__(self, registry):
        self.registry = registry
        self._gate = threading.BoundedSemaphore(1)
        self._approval_snapshots = {}

    def status(self):
        installed = installation()
        features = {"office_templates":True,"geojson":True,"optimization":"ortools" in installed["packages"],"symbolic_math":"sympy" in installed["packages"]}
        features.update({key: key in installed["binaries"] for key in sorted(NATIVE)})
        return {"ready":True,"features":features,"packages":installed["packages"],"binaries":{key:{"version":value["version"],"sha256":value["sha256"]} for key,value in installed["binaries"].items()},"limits":{"input_bytes":MAX_INPUT,"artifact_bytes":MAX_OUTPUT,"worker_memory_bytes":MEMORY_BYTES,"cpu_seconds":CPU_SECONDS,"wall_seconds":WALL_SECONDS,"concurrent_jobs":1},"details":"Finite on-demand production operations. Native services stay off; unavailable engines are not exposed. Worker bounds are not an OS sandbox."}

    def schemas(self):
        from .tools import _schema
        status = self.status()["features"]
        path = {"type":"string","description":"Relative regular file path inside the workspace; newly created output must not already exist."}
        typed_cells={"oneOf":[{"type":"string"},{"type":"integer"},*[{"type":"object","properties":{key:spec},"required":[key],"additionalProperties":False} for key,spec in [("number",{"type":"string","description":"Plain decimal, at most 15 significant digits."}),("sum",{"type":"string","description":"Local numeric-input range, such as B2:B10."}),("sum_product",{"type":"array","items":{"type":"string"},"minItems":2,"maxItems":2,"description":"Two same-shaped ranges of explicit numeric input cells."})]]]}
        result = [_schema("office_create","Create a new macro-free DOCX report, XLSX table or PPTX slide deck. XLSX strings stay literal; typed cells accept integers, {number:'1.25'}, {sum:'B2:B10'} or {sum_product:['B2:B10','C2:C10']}. Only finite local numeric-input formulas; no external references or arbitrary formulas. Numeric precision is limited to 15 significant digits. Available LibreOffice recalculates formula workbooks; PDF accepts internally generated reports only.",{"format":{"type":"string","enum":["docx","xlsx","pptx"]+(["pdf"] if status["libreoffice"] else [])},"path":path,"title":{"type":"string"},"paragraphs":{"type":"array","items":{"type":"string"}},"rows":{"type":"array","items":{"type":"array","items":typed_cells}},"slides":{"type":"array","items":{"type":"object","properties":{"title":{"type":"string"},"body":{"type":"string"}},"required":["title","body"],"additionalProperties":False}}},["format","path","title"]),
                  _schema("geo_analyze","Analyze local WGS84 GeoJSON geometry types, coordinate bounds and feature counts; optional filter_bbox returns only features wholly inside the box. No remote maps, project loading or external references.",{"path":path,"operation":{"type":"string","enum":["summary","filter_bbox"]},"bbox":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4}},["path"])]
        if status["optimization"]:
            result.append(_schema("production_optimize","Solve a bounded integer assignment, machine schedule, bin packing or distance-matrix vehicle route using local OR-Tools. Supply integer units and an explicit local distance matrix; routes never call an online map. Results distinguish feasible from proven optimal.",{"operation":{"type":"string","enum":["assignment","schedule","packing","routing"]},"costs":{"type":"array","items":{"type":"array","items":{"type":"integer"}}},"durations":{"type":"array","items":{"type":"integer"}},"precedence":{"type":"array","items":{"type":"array","items":{"type":"integer"}}},"machines":{"type":"array","items":{"type":"integer"}},"weights":{"type":"array","items":{"type":"integer"}},"capacity":{"type":"integer"},"distance_matrix":{"type":"array","items":{"type":"array","items":{"type":"integer"}}},"vehicles":{"type":"integer"},"depot":{"type":"integer"},"seconds":{"type":"integer","minimum":1,"maximum":15}},["operation"]))
        if status["symbolic_math"]:
            result.append(_schema("production_math","Bounded symbolic mathematics with x,y,z,t,pi,E, literal integer powers -12..12 and sin/cos/tan/exp/log/sqrt/abs. Text is parsed into validated AST constructors, never evaluated as code. solve finds real solutions of expression=0; indefinite integration includes an implicit arbitrary constant.",{"operation":{"type":"string","enum":["simplify","expand","factor","differentiate","integrate","solve","evaluate"]},"expression":{"type":"string","maxLength":1000},"variable":{"type":"string","enum":["x","y","z","t"]},"values":{"type":"object","properties":{x:{"type":"number"} for x in ["x","y","z","t"]},"additionalProperties":False}},["operation","expression"]))
        native_specs = [
            ("freecad","cad_part","Generate a millimeter box, cylinder or four-hole mounting plate using fixed FreeCAD geometry, exported as STEP or STL. No project files, macros, plugins or user scripts.",{"shape":{"type":"string","enum":["box","cylinder","plate_with_holes"]},"dimensions":{"type":"object","properties":{k:{"type":"number","minimum":1,"maximum":1000} for k in ["length","width","height","radius"]},"additionalProperties":False},"format":{"type":"string","enum":["step","stl"]},"path":path},["shape","dimensions","path"]),
            ("blender","render_product","Render a fixed lit primitive product scene to PNG, using factory settings, disabled autoexec, CPU rendering and bounded resolution. No imported scenes or user scripts.",{"shape":{"type":"string","enum":["box","cylinder","sphere"]},"dimensions":{"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3},"color":{"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3},"size":{"type":"integer","minimum":128,"maximum":1024},"path":path},["shape","path"]),
            ("kicad","pcb_manufacture","Generate a blank rectangular PCB outline, run KiCad DRC, and export front/back copper, mask and outline Gerbers in a ZIP with the board and diagnostic report. This template has no components or nets; it is not a complete electronic design.",{"width":{"type":"number","minimum":10,"maximum":200},"height":{"type":"number","minimum":10,"maximum":200},"path":path},["width","height","path"]),
            ("qgis","gis_buffer","Buffer a validated, local WGS84 GeoJSON collection with QGIS native:buffer. Distance is explicitly in geographic degrees, not meters. New GeoJSON output only; no project, provider URI or plugin arguments.",{"path":path,"output":path,"distance_degrees":{"type":"number","minimum":.000001,"maximum":1}},["path","output","distance_degrees"]),
            ("ffmpeg","media_transform","Transform a small local media snapshot to mono 16 kHz PCM WAV, one PNG thumbnail, or a 720p H.264/AAC MP4 clip of at most 120 seconds. Fixed protocols and codecs; no URLs, playlists, user filters or command arguments.",{"path":path,"output":path,"operation":{"type":"string","enum":["audio_wav","thumbnail","clip_mp4"]},"start":{"type":"number","minimum":0,"maximum":3600},"duration":{"type":"number","minimum":.1,"maximum":120}},["path","output","operation"])]
        for engine,name,description,properties,required in native_specs:
            if status[engine]:
                result.append(_schema(name,description,properties,required))
        return result

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown production tool.")
        return "approval" if name in {"office_create","cad_part","render_product","pcb_manufacture","gis_buffer","media_transform"} else "auto"

    def fingerprint_material(self, name, args):
        """Bind approval to immutable input bytes and the selected fixed engines.

        The registry calls this again while checking approval before execute.
        Keep the exact last-checked snapshot so an external edit between that
        check and execution cannot silently change the approved input bytes.
        """
        clean = validate_args(name,args)
        installed = installation()
        source = self._snapshot(clean["path"]) if name in {"gis_buffer","media_transform"} else None
        key = hashlib.sha256(json.dumps([name,clean],sort_keys=True,allow_nan=False).encode()).hexdigest()
        with self.registry._lock:
            if self.policy(name,clean) == "approval":
                if len(self._approval_snapshots) >= 2: self._approval_snapshots.clear()
                self._approval_snapshots[key] = {"source":source,"installation":installed}
            output = clean.get("output") if name in {"gis_buffer","media_transform"} else clean.get("path") if self.policy(name,clean)=="approval" else None
            destination = self.registry._path(output) if output else None
            existing = None
            if destination and destination.exists():
                info = destination.stat()
                existing = {"bytes":info.st_size,"mtime_ns":info.st_mtime_ns,"inode":info.st_ino}
        return {"name":name,"args":clean,"engines":installed,"source":{k:v for k,v in source.items() if k!="data"} if source else None,"output_exists":existing,
                "worker_sha256":hashlib.sha256(Path(__file__).with_name("_production_worker.py").read_bytes()).hexdigest(),
                "adapter_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "manifest_sha256":hashlib.sha256(MANIFEST.read_bytes()).hexdigest() if MANIFEST.exists() else None}

    def _snapshot(self, relative):
        path = self.registry._path(relative)
        with self.registry._lock, self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path,directory),"rb") as stream:
                before = os.fstat(stream.fileno())
                if before.st_size > MAX_INPUT:
                    raise ValueError("Production input exceeds 20 MB.")
                data = stream.read(MAX_INPUT + 1)
                after = os.fstat(stream.fileno())
                if len(data) > MAX_INPUT or (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
                    raise ValueError("Production input changed or exceeded its snapshot bound.")
        return {"path":relative,"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data),"data":base64.b64encode(data).decode("ascii")}

    def _commit(self, relative, data):
        path = self.registry._path(relative)
        temporary = ".production-" + secrets.token_hex(12) + ".tmp"
        with self.registry._lock, self.registry._directory(path.parent,create=True) as directory:
            self.registry._check_ancestors(path)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_NOFOLLOW",0) | getattr(os,"O_BINARY",0)
            temp = path.parent / temporary
            descriptor = os.open(temp if directory is None else temporary,flags,0o600,**({} if directory is None else {"dir_fd":directory}))
            try:
                with os.fdopen(descriptor,"wb") as output:
                    output.write(data); output.flush(); os.fsync(output.fileno())
                if directory is None:
                    # rename is exclusive on Windows; POSIX uses an exclusive link.
                    os.rename(temp,path)
                else:
                    os.link(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
            finally:
                try:
                    if directory is None: temp.unlink()
                    else: os.unlink(temporary,dir_fd=directory)
                except FileNotFoundError:
                    pass
        return {"path":relative,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}

    def execute(self, name, args, *, cancelled=None):
        from .worker import run_worker
        try:
            clean = validate_args(name,args)
            key = hashlib.sha256(json.dumps([name,clean],sort_keys=True,allow_nan=False).encode()).hexdigest()
            with self.registry._lock:
                checked = self._approval_snapshots.pop(key,None) if self.policy(name,clean)=="approval" else None
            installed = checked["installation"] if checked else installation()
            required = {"production_optimize":"ortools","production_math":"sympy"}.get(name)
            engine = {"cad_part":"freecad","render_product":"blender","pcb_manufacture":"kicad","gis_buffer":"qgis","media_transform":"ffmpeg"}.get(name)
            if name == "office_create" and clean["format"] == "pdf": engine = "libreoffice"
            if name == "office_create" and clean["format"] == "xlsx" and any(type(cell) is dict and ('sum' in cell or 'sum_product' in cell) for row in clean['rows'] for cell in row) and 'libreoffice' in installed['binaries']:
                engine = 'libreoffice'
            if required and required not in installed["packages"] or engine and engine not in installed["binaries"]:
                raise ValueError("This optional engine has not been provisioned and verified; no download occurs during a tool call.")
            output = clean.get("output") if name in {"gis_buffer","media_transform"} else clean.get("path") if name in {"office_create","cad_part","render_product","pcb_manufacture"} else None
            if output:
                if name == "office_create": expected = "." + clean["format"]
                elif name == "cad_part": expected = "." + clean["format"]
                elif name == "media_transform": expected = {"audio_wav":".wav","thumbnail":".png","clip_mp4":".mp4"}[clean["operation"]]
                else: expected = {"render_product":".png","pcb_manufacture":".zip","gis_buffer":".geojson"}[name]
                if Path(output).suffix.lower() != expected:
                    raise ValueError("Output extension must match the fixed artifact format: " + expected)
                destination = self.registry._path(output)
                if destination.exists():
                    raise ValueError("Production artifacts never overwrite existing files. Choose a new output path.")
            if not self._gate.acquire(blocking=False):
                raise ValueError("A production job is already running; retry after it finishes.")
            try:
                source = None
                if name in {"gis_buffer","media_transform"}:
                    source = checked["source"] if checked else None
                    # Direct callers receive a fresh immutable snapshot. The
                    # agent registry supplies the snapshot checked by approval.
                    if source is None: source = self._snapshot(clean["path"])
                elif name == "geo_analyze": source = self._snapshot(clean["path"])
                parent = ROOT / "data/worker-tmp"
                self.registry._check_ancestors(parent)
                parent.mkdir(parents=True,exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="production-",dir=parent) as temporary, self.registry._directory(Path(temporary)):
                    payload = {"name":name,"args":clean,"source":source,"directory":temporary,"engine":installed["binaries"].get(engine),"packages":installed["packages"]}
                    raw = run_worker(Path(__file__).with_name("_production_worker.py"),json.dumps(payload,allow_nan=False).encode(),timeout=WALL_SECONDS,memory_bytes=MEMORY_BYTES,cpu_seconds=CPU_SECONDS,max_output_bytes=200_000,max_input_bytes=MAX_REQUEST,process_limit=4 if engine else 1,cancelled=cancelled)
                    result = json.loads(raw)
                    if result.get("ok") and output:
                        artifact = Path(temporary)/"artifact"
                        with self.registry._directory(artifact.parent) as directory:
                            with os.fdopen(self.registry._open_read(artifact,directory),"rb") as stream:
                                data = stream.read(MAX_OUTPUT+1)
                        if not data or len(data)>MAX_OUTPUT:
                            raise ValueError("Generated artifact was empty or exceeded 20 MB.")
                        if cancelled and cancelled():
                            raise ValueError("Production operation cancelled before artifact commit.")
                        result["artifact"] = self._commit(output,data)
                    if source:
                        result["source"] = {k:v for k,v in source.items() if k != "data"}
                    return result
            finally:
                self._gate.release()
        except Exception as error:
            from .worker import WorkerError
            if isinstance(error,(ValueError,OSError,WorkerError)):
                return {"ok":False,"error":str(error)}
            return {"ok":False,"error":"Production operation failed within its fixed input, output or runtime limits."}
