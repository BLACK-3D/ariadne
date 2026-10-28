"""Fixed child implementation of finite production operations; no user code."""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from ariadne.production_tools import (ROOT, VENDOR, MAX_INPUT, MAX_OUTPUT, MAX_REQUEST,
                                      validate_args, _local, obj, number, spreadsheet_cells)


def _strict(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not accepted.")
        result[key] = value
    return result


def _reject(value):
    raise ValueError("Non-finite JSON constants are not accepted.")


def _load_json(raw):
    return json.loads(raw,object_pairs_hook=_strict,parse_constant=_reject)


def source_bytes(source):
    obj(source,{"path","sha256","bytes","data"},{"path","sha256","bytes","data"})
    raw = base64.b64decode(source["data"],validate=True)
    if len(raw)>MAX_INPUT or len(raw)!=source["bytes"] or hashlib.sha256(raw).hexdigest()!=source["sha256"]:
        raise ValueError("Input snapshot failed its byte/digest check.")
    return raw


def optimize(args):
    from ortools.sat.python import cp_model
    kind = args["operation"]
    if kind == "routing":
        from ortools.constraint_solver import pywrapcp,routing_enums_pb2
        matrix = args["distance_matrix"]
        manager = pywrapcp.RoutingIndexManager(len(matrix),args["vehicles"],args["depot"])
        routing = pywrapcp.RoutingModel(manager)
        callback = routing.RegisterTransitCallback(lambda a,b: matrix[manager.IndexToNode(a)][manager.IndexToNode(b)])
        routing.SetArcCostEvaluatorOfAllVehicles(callback)
        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.seconds = args["seconds"]
        solution = routing.SolveWithParameters(params)
        if not solution:
            return {"ok":True,"engine":"ortools","status":"NO_SOLUTION_WITHIN_LIMIT","optimal":False,"routes":[]}
        routes=[]
        for vehicle in range(args["vehicles"]):
            index=routing.Start(vehicle); route=[]
            while not routing.IsEnd(index):
                route.append(manager.IndexToNode(index)); index=solution.Value(routing.NextVar(index))
            route.append(manager.IndexToNode(index)); routes.append(route)
        return {"ok":True,"engine":"ortools","status":"FEASIBLE","optimal":False,"objective":solution.ObjectiveValue(),"routes":routes,"units":"caller-supplied integer distances"}
    model=cp_model.CpModel()
    if kind == "assignment":
        costs=args["costs"]; workers=len(costs); tasks=len(costs[0])
        variables=[[model.NewBoolVar(f"w{i}_t{j}") for j in range(tasks)] for i in range(workers)]
        for i in range(workers): model.Add(sum(variables[i])<=1)
        for j in range(tasks): model.Add(sum(variables[i][j] for i in range(workers))==1)
        model.Minimize(sum(costs[i][j]*variables[i][j] for i in range(workers) for j in range(tasks)))
    elif kind == "packing":
        weights=args["weights"]; count=len(weights)
        used=[model.NewBoolVar(f"b{j}") for j in range(count)]
        variables=[[model.NewBoolVar(f"i{i}_b{j}") for j in range(count)] for i in range(count)]
        for i in range(count): model.Add(sum(variables[i])==1)
        for j in range(count):
            model.Add(sum(weights[i]*variables[i][j] for i in range(count))<=args["capacity"]*used[j])
            if j: model.Add(used[j-1]>=used[j])
        model.Minimize(sum(used))
    else:
        durations=args["durations"]; horizon=sum(durations)
        starts=[model.NewIntVar(0,horizon-d,f"s{i}") for i,d in enumerate(durations)]
        ends=[model.NewIntVar(d,horizon,f"e{i}") for i,d in enumerate(durations)]
        intervals=[model.NewIntervalVar(starts[i],d,ends[i],f"t{i}") for i,d in enumerate(durations)]
        for machine in set(args["machines"]):
            model.AddNoOverlap([intervals[i] for i,m in enumerate(args["machines"]) if m==machine])
        for before,after in args["precedence"]: model.Add(ends[before]<=starts[after])
        makespan=model.NewIntVar(0,horizon,"makespan"); model.AddMaxEquality(makespan,ends); model.Minimize(makespan)
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=args["seconds"]
    solver.parameters.num_search_workers=2; solver.parameters.random_seed=0
    status=solver.Solve(model)
    result={"ok":True,"engine":"ortools","status":solver.StatusName(status),"optimal":status==cp_model.OPTIMAL,"units":"caller-supplied integers"}
    if status not in {cp_model.FEASIBLE,cp_model.OPTIMAL}: return result
    result.update(objective=round(solver.ObjectiveValue()),best_bound=solver.BestObjectiveBound())
    if kind == "assignment":
        result["assignments"]=[{"worker":i,"task":j,"cost":costs[i][j]} for i in range(workers) for j in range(tasks) if solver.Value(variables[i][j])]
    elif kind == "packing":
        result["bins"]=[{"items":[i for i in range(count) if solver.Value(variables[i][j])],"weight":sum(weights[i] for i in range(count) if solver.Value(variables[i][j]))} for j in range(count) if solver.Value(used[j])]
    else:
        result["tasks"]=[{"task":i,"machine":args["machines"][i],"start":solver.Value(starts[i]),"end":solver.Value(ends[i])} for i in range(len(durations))]
    return result


def symbolic(args):
    import sympy as s
    symbols={name:s.Symbol(name,real=True) for name in ["x","y","z","t"]}
    names={**symbols,"pi":s.pi,"E":s.E}
    functions={"sin":s.sin,"cos":s.cos,"tan":s.tan,"exp":s.exp,"log":s.log,"sqrt":s.sqrt,"abs":s.Abs}
    def construct(node):
        if isinstance(node,ast.Constant): return s.Rational(str(node.value))
        if isinstance(node,ast.Name): return names[node.id]
        if isinstance(node,ast.UnaryOp):
            value=construct(node.operand)
            return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.Call): return functions[node.func.id](construct(node.args[0]))
        left,right=construct(node.left),construct(node.right)
        if isinstance(node.op,ast.Add): return s.Add(left,right)
        if isinstance(node.op,ast.Sub): return s.Add(left,-right)
        if isinstance(node.op,ast.Mult): return s.Mul(left,right)
        if isinstance(node.op,ast.Div): return s.Mul(left,s.Pow(right,-1))
        return s.Pow(left,right)
    expression=construct(ast.parse(args["expression"],mode="eval").body)
    operation=args["operation"]; variable=symbols[args["variable"]]
    if operation=="evaluate":
        result=expression.subs({symbols[k]:s.Rational(str(v)) for k,v in args["values"].items()})
        if result.free_symbols: raise ValueError("Provide a value for every symbol when evaluating.")
        if result.has(s.zoo,s.nan,s.oo,-s.oo) or result.is_real is not True:
            raise ValueError("Expression is undefined, non-real, or non-finite at the supplied values.")
        answer={"exact":str(result),"decimal":str(s.N(result,20))}
    elif operation=="solve":
        if expression.free_symbols-{variable}: raise ValueError("Solve accepts a single symbolic variable.")
        result=s.solveset(expression,variable,domain=s.S.Reals)
        answer={"solution_set":str(result),"complete":not result.has(s.ConditionSet)}
    else:
        result={"simplify":s.simplify,"expand":s.expand,"factor":s.factor,"differentiate":lambda e:s.diff(e,variable),"integrate":lambda e:s.integrate(e,variable)}[operation](expression)
        answer={"expression":str(result)}
        if operation=="integrate": answer["arbitrary_constant"]="Add an arbitrary integration constant."
    if any(len(value)>16000 for value in answer.values() if isinstance(value,str)):
        raise ValueError("Symbolic result exceeded its output limit.")
    return {"ok":True,"engine":"sympy","operation":operation,**answer}


def _zip(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,body in entries.items(): archive.writestr(name,body)
    return output.getvalue()


XML='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
OREL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def office_bytes(args):
    """Create data-only OOXML. No macros, formulas, external links or embedded files."""
    fmt=args["format"]
    if fmt in {"docx","pdf"}:
        paragraphs=[args["title"],*args["paragraphs"]]
        body=''.join('<w:p><w:r><w:t xml:space="preserve">'+escape(value)+'</w:t></w:r></w:p>' for value in paragraphs)
        return _zip({'[Content_Types].xml':XML+f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            '_rels/.rels':XML+f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OREL}/officeDocument" Target="word/document.xml"/></Relationships>',
            'word/document.xml':XML+'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'})
    if fmt=="xlsx":
        def column(index):
            result=''
            while index:
                index,remainder=divmod(index-1,26); result=chr(65+remainder)+result
            return result
        cells,cached=spreadsheet_cells(args['rows'])
        def cell(value,row,col):
            reference=f'{column(col)}{row}'
            if type(value) is str: return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
            if 'number' in value: return f'<c r="{reference}"><v>{value["number"]}</v></c>'
            formula='SUM('+value['sum']+')' if 'sum' in value else 'SUMPRODUCT('+','.join(value['sum_product'])+')'
            return f'<c r="{reference}"><f>{formula}</f><v>{cached[row,col]}</v></c>'
        rows=''.join('<row r="'+str(i)+'">'+''.join(cell(value,i,j) for j,value in enumerate(row,1))+'</row>' for i,row in enumerate(cells,1))
        return _zip({'[Content_Types].xml':XML+f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
            '_rels/.rels':XML+f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OREL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
            'xl/workbook.xml':XML+f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{OREL}"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>',
            'xl/_rels/workbook.xml.rels':XML+f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OREL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/worksheets/sheet1.xml':XML+'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData></worksheet>'})
    ns='http://schemas.openxmlformats.org/presentationml/2006/main'
    ans='http://schemas.openxmlformats.org/drawingml/2006/main'
    entries={'_rels/.rels':XML+f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OREL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'}
    overrides='<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    slide_ids=''; relationships=''
    for index,slide in enumerate(args["slides"],1):
        slide_ids+=f'<p:sldId id="{255+index}" r:id="rId{index}"/>'
        relationships+=f'<Relationship Id="rId{index}" Type="{OREL}/slide" Target="slides/slide{index}.xml"/>'
        overrides+=f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        shapes=''
        for shape_id,value,y,height,size in [(2,slide["title"],400000,1000000,3200),(3,slide["body"],1600000,4500000,1800)]:
            paragraphs=''.join(f'<a:p><a:r><a:rPr lang="en-US" sz="{size}"/><a:t>{escape(line)}</a:t></a:r></a:p>' for line in value.split('\n'))
            shapes+=f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Text {shape_id}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="600000" y="{y}"/><a:ext cx="10900000" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{paragraphs}</p:txBody></p:sp>'
        entries[f'ppt/slides/slide{index}.xml']=XML+f'<p:sld xmlns:a="{ans}" xmlns:r="{OREL}" xmlns:p="{ns}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    entries['[Content_Types].xml']=XML+f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>'
    entries['ppt/presentation.xml']=XML+f'<p:presentation xmlns:p="{ns}" xmlns:r="{OREL}"><p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    entries['ppt/_rels/presentation.xml.rels']=XML+f'<Relationships xmlns="{REL}">{relationships}</Relationships>'
    return _zip(entries)


def geojson(raw):
    if len(raw)>2_000_000: raise ValueError("GeoJSON exceeds its 2 MB limit.")
    data=_load_json(raw)
    obj(data,{"type","features","name","bbox","crs","xy_coordinate_resolution"},{"type","features"})
    if "xy_coordinate_resolution" in data:
        number(data['xy_coordinate_resolution'],0,1,'coordinate resolution')
    if "crs" in data:
        crs=obj(data['crs'],{'type','properties'},{'type','properties'})
        obj(crs['properties'],{'name'},{'name'})
        if crs['type']!='name' or crs['properties']['name'] not in {'urn:ogc:def:crs:OGC:1.3:CRS84','urn:ogc:def:crs:EPSG::4326','EPSG:4326','OGC:CRS84'}:
            raise ValueError('Only WGS84 GeoJSON is supported; remote or projected CRS definitions are rejected.')
    if data["type"]!="FeatureCollection" or type(data["features"]) is not list or len(data["features"])>5000:
        raise ValueError("Supply a FeatureCollection with at most 5,000 features.")
    clean=[]; coordinate_count=0
    def geometry(value):
        nonlocal coordinate_count
        obj(value,{"type","coordinates"},{"type","coordinates"})
        depths={"Point":0,"MultiPoint":1,"LineString":1,"MultiLineString":2,"Polygon":2,"MultiPolygon":3}
        kind=value["type"]
        if type(kind) is not str or kind not in depths: raise ValueError("Unsupported GeoJSON geometry type.")
        def coords(items,depth):
            nonlocal coordinate_count
            if type(items) is not list or not items: raise ValueError("Empty or malformed coordinates.")
            if depth:
                if len(items)>10000: raise ValueError("Coordinate group is too large.")
                return [coords(x,depth-1) for x in items]
            if len(items)!=2: raise ValueError("Only two-dimensional WGS84 coordinates are supported.")
            coordinate_count+=1
            if coordinate_count>50000: raise ValueError("Too many GeoJSON coordinates.")
            return [number(items[0],-180,180,"longitude"),number(items[1],-90,90,"latitude")]
        return {"type":kind,"coordinates":coords(value["coordinates"],depths[kind])}
    for feature in data["features"]:
        obj(feature,{"type","geometry","properties","id","bbox"},{"type","geometry"})
        if feature["type"]!="Feature": raise ValueError("Invalid GeoJSON feature.")
        properties=feature.get("properties") or {}
        if type(properties) is not dict or len(properties)>30: raise ValueError("Feature properties must be a small flat object.")
        for k,v in properties.items():
            if type(k) is not str or len(k)>100 or type(v) not in {str,int,float,bool,type(None)}:
                raise ValueError("Feature properties must contain bounded scalar data.")
            if isinstance(v,str) and len(v)>1000 or type(v) is float and not math.isfinite(v):
                raise ValueError("Invalid feature property.")
        clean.append({"type":"Feature","geometry":geometry(feature["geometry"]),"properties":properties})
    return {"type":"FeatureCollection","features":clean}


def _points(coordinates):
    if type(coordinates[0]) in {int,float}: yield coordinates
    else:
        for value in coordinates: yield from _points(value)


def geospatial(args,raw):
    data=geojson(raw); types={}; bounds=None; selected=[]
    for feature in data["features"]:
        g=feature["geometry"]; types[g["type"]]=types.get(g["type"],0)+1
        points=list(_points(g["coordinates"]))
        west=min(p[0] for p in points); east=max(p[0] for p in points); south=min(p[1] for p in points); north=max(p[1] for p in points)
        bounds=[west,south,east,north] if bounds is None else [min(bounds[0],west),min(bounds[1],south),max(bounds[2],east),max(bounds[3],north)]
        if args["operation"]=="filter_bbox":
            box=args["bbox"]
            if west>=box[0] and south>=box[1] and east<=box[2] and north<=box[3]: selected.append(feature)
    result={"ok":True,"engine":"builtin_geojson","crs":"OGC:CRS84 (longitude,latitude)","features":len(data["features"]),"geometry_types":types,"bbox":bounds}
    if args["operation"]=="filter_bbox":
        result["selected_features"]=len(selected); result["collection"]={"type":"FeatureCollection","features":selected}
        if len(json.dumps(result).encode())>150000: raise ValueError("Filtered features exceed the return limit; use a smaller selection.")
    return result


def native_run(engine,arguments,stage, *, success_codes=(0,)):
    path=_local(engine["path"])
    with path.open("rb") as stream:
        if hashlib.file_digest(stream,"sha256").hexdigest()!=engine["sha256"]:
            raise ValueError("Native executable changed after qualification.")
    # No shell, inherited arguments, plugin script, URLs or owner environment.
    environment=dict(os.environ,QT_QPA_PLATFORM='offscreen',QGIS_PROCESS_DISABLE_PYTHON_PLUGINS='1',QGIS_CUSTOM_CONFIG_PATH=str(stage/'qgis-profile'))
    if path.name.lower()=='kicad-cli.exe':
        environment.update(KICAD_CONFIG_HOME=str(stage/'kicad-config'),KICAD_DOCUMENTS_HOME=str(stage/'kicad-documents'),KICAD_CACHE_HOME=str(stage/'kicad-cache'),KICAD_STOCK_DATA_HOME=str(path.parent.parent/'share/kicad'))
    if path.name.lower()=='qgis_process.exe':
        prefix=path.parent.parent
        distribution=prefix.parent.parent
        environment.update(QGIS_PREFIX_PATH=str(prefix),GDAL_DATA=str(distribution/'share/gdal'),PROJ_DATA=str(distribution/'share/proj'),PROJ_NETWORK='OFF')
        qt=next((distribution/'apps'/name for name in ['Qt6','Qt5'] if (distribution/'apps'/name).is_dir()),None)
        additions=[str(distribution/'bin'),str(path.parent)]
        if qt:
            additions.append(str(qt/'bin')); environment['QT_PLUGIN_PATH']=str(qt/'plugins')
        environment['PATH']=os.pathsep.join([*additions,environment['PATH']])
    completed=subprocess.run([str(path),*arguments],cwd=stage,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=65,env=environment,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if completed.returncode not in success_codes:
        raise ValueError("The fixed native operation failed; no artifact was committed.")
    return completed.returncode


FREECAD_SCRIPT = '''import FreeCAD as A, Part, Mesh, json
from pathlib import Path
p=Path(__file__).parent
a=json.loads((p/'parameters.json').read_text(encoding='utf-8')); d=a['dimensions']
if a['shape']=='cylinder': shape=Part.makeCylinder(d['radius'],d['height'])
else:
 shape=Part.makeBox(d['length'],d['width'],d['height'])
 if a['shape']=='plate_with_holes':
  radius=min(d['length'],d['width'])/20
  for x in [d['length']*.15,d['length']*.85]:
   for y in [d['width']*.15,d['width']*.85]: shape=shape.cut(Part.makeCylinder(radius,d['height'],A.Vector(x,y,0)))
doc=A.newDocument('AriadnePart'); item=doc.addObject('Part::Feature','Part'); item.Shape=shape; doc.recompute()
if a['format']=='step': Part.export([item],str(p/'artifact.step')); (p/'artifact.step').rename(p/'artifact')
else: Mesh.export([item],str(p/'artifact.stl')); (p/'artifact.stl').rename(p/'artifact')
A.closeDocument(doc.Name)
'''

BLENDER_SCRIPT = '''import bpy,json,math
from pathlib import Path
from mathutils import Vector
p=Path(__file__).parent; a=json.loads((p/'parameters.json').read_text(encoding='utf-8'))
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
if a['shape']=='box': bpy.ops.mesh.primitive_cube_add()
elif a['shape']=='cylinder': bpy.ops.mesh.primitive_cylinder_add(vertices=48)
else: bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=16)
obj=bpy.context.object; obj.dimensions=a['dimensions']; bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
mat=bpy.data.materials.new('Product'); mat.diffuse_color=(*a['color'],1); obj.data.materials.append(mat)
span=max(a['dimensions']); bpy.ops.object.camera_add(location=(span*2.2,-span*2.2,span*1.6)); camera=bpy.context.object
camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler(); bpy.context.scene.camera=camera
bpy.ops.object.light_add(type='AREA',location=(span,-span,span*3)); bpy.context.object.data.energy=500; bpy.context.object.data.shape='DISK'; bpy.context.object.data.size=span*2
scene=bpy.context.scene; scene.world.color=(.12,.12,.12); scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=16
scene.render.threads_mode='FIXED'; scene.render.threads=2; scene.render.resolution_x=a['size']; scene.render.resolution_y=a['size']; scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'; scene.render.filepath=str(p/'artifact'); scene.render.use_file_extension=False
bpy.ops.render.render(write_still=True)
'''


def native_operation(name,args,engine,stage,raw=None):
    artifact=stage/'artifact'
    if name=='render_product' and engine.get('kind')=='bpy':
        return render_bpy(args,stage)
    if name=="office_create":
        source=stage/'report.docx'; source.write_bytes(office_bytes(args))
        profile=(stage/'lo-profile').as_uri()
        native_run(engine,[f'-env:UserInstallation={profile}','--headless','--norestore','--nodefault','--nolockcheck','--convert-to','pdf:writer_pdf_Export','--outdir',str(stage),str(source)],stage)
        (stage/'report.pdf').rename(artifact)
    elif name in {"cad_part","render_product"}:
        (stage/'parameters.json').write_text(json.dumps(args),encoding='utf-8')
        script=stage/'fixed_job.py'; script.write_text(FREECAD_SCRIPT if name=="cad_part" else BLENDER_SCRIPT,encoding='utf-8')
        native_run(engine,[str(script)] if name=="cad_part" else ['--factory-startup','--background','--disable-autoexec','--threads','2','--python',str(script)],stage)
    elif name=="pcb_manufacture":
        board=stage/'board.kicad_pcb'; w=args['width']; h=args['height']
        board.write_text(f'''(kicad_pcb (version 20260101) (generator "ariadne")
 (general (thickness 1.6)) (paper "A4")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user))
 (setup (pad_to_mask_clearance 0))
 (gr_rect (start 20 20) (end {20+w} {20+h}) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts"))
)''',encoding='utf-8')
        native_run(engine,['pcb','drc','--format','json','--output',str(stage/'drc.json'),str(board)],stage,success_codes=(0,5))
        gerbers=stage/'gerbers'; gerbers.mkdir()
        native_run(engine,['pcb','export','gerbers','--layers','F.Cu,B.Cu,F.Mask,B.Mask,Edge.Cuts','--output',str(gerbers)+os.sep,str(board)],stage)
        entries={'board.kicad_pcb':board.read_bytes(),'drc.json':(stage/'drc.json').read_bytes()}
        for path in gerbers.iterdir():
            if path.is_file() and path.stat().st_size<MAX_OUTPUT: entries['gerbers/'+path.name]=path.read_bytes()
        artifact.write_bytes(_zip(entries))
        return {"ok":True,"engine":"kicad","template":"blank rectangular board","drc_report":"drc.json inside artifact; inspect before manufacturing"}
    elif name=="gis_buffer":
        data=geojson(raw); source=stage/'input.geojson'; source.write_text(json.dumps(data),encoding='utf-8')
        out=stage/'buffer.geojson'
        native_run(engine,['--no-python','--skip-loading-plugins','run','native:buffer','--','INPUT='+str(source),'DISTANCE='+str(args['distance_degrees']),'SEGMENTS=8','END_CAP_STYLE=0','JOIN_STYLE=0','MITER_LIMIT=2','DISSOLVE=false','OUTPUT='+str(out)],stage)
        generated=geojson(out.read_bytes()); artifact.write_text(json.dumps(generated),encoding='utf-8')
    elif name=="media_transform":
        # An explicit demuxer and restrictive protocol list reject playlists,
        # concat manifests, SVG and arbitrary format autodetection.
        if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE': fmt='wav'
        elif raw[:4]==b'fLaC': fmt='flac'
        elif raw[:4]==b'OggS': fmt='ogg'
        elif raw[:4]==bytes.fromhex('1a45dfa3'): fmt='matroska'
        elif len(raw)>12 and raw[4:8]==b'ftyp': fmt='mov'
        elif raw[:3]==b'ID3' or raw[:2] in {b'\xff\xfb',b'\xff\xf3',b'\xff\xf2'}: fmt='mp3'
        else: raise ValueError("Media snapshot is not a supported WAV/FLAC/Ogg/MP3/MP4/Matroska file.")
        source=stage/'input.media'; source.write_bytes(raw)
        options=['-nostdin','-hide_banner','-loglevel','error','-y','-threads','2','-filter_threads','2','-protocol_whitelist','file','-f',fmt]
        if fmt=='mov': options+=['-enable_drefs','0','-use_absolute_path','0']
        options+=['-ss',str(args['start']),'-i',str(source),'-t',str(args['duration']),'-threads','2','-map_metadata','-1','-map_chapters','-1','-fs',str(MAX_OUTPUT)]
        operation=args['operation']
        if operation=='audio_wav': options+=['-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-f','wav']
        elif operation=='thumbnail': options+=['-map','0:v:0','-an','-frames:v','1','-vf','scale=720:720:force_original_aspect_ratio=decrease','-c:v','png','-f','image2']
        else: options+=['-map','0:v:0','-map','0:a:0?','-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libx264','-preset','veryfast','-crf','24','-c:a','aac','-b:a','96k','-movflags','+faststart','-f','mp4']
        native_run(engine,[*options,str(artifact)],stage)
    return {"ok":True,"engine":{"cad_part":"freecad","render_product":"blender","gis_buffer":"qgis","media_transform":"ffmpeg","office_create":"libreoffice"}[name]}


def render_bpy(args,stage):
    """Render with the official pinned bpy wheel, entirely inside this worker.

    No scene import, user Python, plugin loading, provider callbacks, downloads
    or GPU selection crosses the tool boundary. Fixed CPU Cycles settings keep
    a graphics card available to the selected conversational model.
    """
    from ariadne.config import _allowed_path
    vendor=_allowed_path(ROOT/'runtime/production/blender-python')
    sys.path.insert(0,str(vendor))
    import bpy
    from mathutils import Vector
    if tuple(bpy.app.version[:3])!=(5,2,3):
        raise ValueError('Blender Python engine version differs from its reviewed profile.')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if args['shape']=='box': bpy.ops.mesh.primitive_cube_add()
    elif args['shape']=='cylinder': bpy.ops.mesh.primitive_cylinder_add(vertices=48)
    else: bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=16)
    item=bpy.context.object; item.dimensions=args['dimensions']
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    material=bpy.data.materials.new('AriadneProduct')
    material.diffuse_color=(*args['color'],1); item.data.materials.append(material)
    span=max(args['dimensions'])
    bpy.ops.object.camera_add(location=(span*2.2,-span*2.2,span*1.6))
    camera=bpy.context.object
    camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
    scene=bpy.context.scene; scene.camera=camera
    bpy.ops.object.light_add(type='AREA',location=(span,-span,span*3))
    light=bpy.context.object; light.data.energy=500*span*span
    light.data.shape='DISK'; light.data.size=span*2
    scene.world=bpy.data.worlds.new('AriadneWorld'); scene.world.color=(.12,.12,.12)
    scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=16
    scene.render.threads_mode='FIXED'; scene.render.threads=2
    scene.render.resolution_x=args['size']; scene.render.resolution_y=args['size']; scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'; scene.render.filepath=str(stage/'artifact'); scene.render.use_file_extension=False
    # Native engines log to stdout. Preserve the JSON protocol by redirecting
    # the OS descriptor as well as Python's stream for the render itself.
    sys.stdout.flush(); saved=os.dup(1)
    try:
        with open(os.devnull,'w') as quiet:
            os.dup2(quiet.fileno(),1)
            bpy.ops.render.render(write_still=True)
    finally:
        os.dup2(saved,1); os.close(saved)
    return {'ok':True,'engine':'blender_bpy','version':'5.2.3','device':'CPU','samples':16,'size':args['size']}


def run(payload):
    obj(payload,{"name","args","source","directory","engine","packages"},{"name","args","directory","packages"})
    name=payload['name']; args=validate_args(name,payload['args'])
    stage=_local_directory(payload['directory'])
    if name in {'production_optimize','production_math'}:
        sys.path.insert(0,str(VENDOR))
        return optimize(args) if name=='production_optimize' else symbolic(args)
    if name=='geo_analyze': return geospatial(args,source_bytes(payload['source']))
    if name=='office_create' and args['format']!='pdf':
        formulas=args['format']=='xlsx' and any(type(cell) is dict and ('sum' in cell or 'sum_product' in cell) for row in args['rows'] for cell in row)
        raw=office_bytes(args)
        recalculated=False
        if formulas and payload.get('engine'):
            source=stage/'workbook.xlsx'; source.write_bytes(raw)
            converted=stage/'converted'; converted.mkdir()
            profile=(stage/'lo-profile').as_uri()
            native_run(payload['engine'],[f'-env:UserInstallation={profile}','--headless','--norestore','--nodefault','--nolockcheck','--convert-to','xlsx:Calc MS Excel 2007 XML','--outdir',str(converted),str(source)],stage)
            generated=converted/'workbook.xlsx'
            if generated.stat().st_size>MAX_OUTPUT: raise ValueError('Recalculated workbook exceeds its artifact limit.')
            raw=generated.read_bytes(); recalculated=True
        (stage/'artifact').write_bytes(raw)
        return {"ok":True,"engine":"libreoffice_calc" if recalculated else "builtin_ooxml","format":args['format'],"macros":False,"external_links":False,"formulas":bool(formulas),"recalculated":recalculated,"numeric_precision_digits":15 if args['format']=='xlsx' else None}
    raw=source_bytes(payload['source']) if name in {'gis_buffer','media_transform'} else None
    return native_operation(name,args,payload['engine'],stage,raw)


def _local_directory(value):
    from ariadne.config import _allowed_path
    path=_allowed_path(value)
    if path.parent!=ROOT/'data/worker-tmp' or not path.name.startswith('production-') or not path.is_dir():
        raise ValueError("Invalid fixed production staging directory.")
    return path


def main():
    started=time.monotonic()
    try:
        raw=sys.stdin.buffer.read(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST: raise ValueError("Production request exceeds its byte bound.")
        result=run(_load_json(raw))
    except ValueError as error:
        result={"ok":False,"error":str(error)}
    except Exception:
        result={"ok":False,"error":"Fixed production worker failed within its input, native-engine or resource limits; no artifact was committed."}
    result['elapsed_ms']=round((time.monotonic()-started)*1000)
    encoded=json.dumps(result,allow_nan=False,ensure_ascii=False).encode('utf-8')
    if len(encoded)>180000: encoded=b'{"ok":false,"error":"Production result exceeded its output bound."}'
    sys.stdout.buffer.write(encoded); sys.stdout.buffer.flush()


if __name__=='__main__':
    if sys.argv[1:]: raise SystemExit("Fixed internal production worker accepts no arguments.")
    main()
