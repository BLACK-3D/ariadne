import hashlib
import io
import itertools
import json
import math
import subprocess
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from ariadne.config import Settings
from ariadne.tools import ToolRegistry
from ariadne.production_tools import (ProductionTools, installation, validate_args,
                                      validate_math, validate_optimization, spreadsheet_cells)
from ariadne._production_worker import geojson, office_bytes, symbolic, native_operation


class ProductionValidationTests(unittest.TestCase):
    def test_math_never_accepts_code_or_open_ended_functions(self):
        for expression in ["__import__('os').system('x')","x.__class__","[x for x in y]","x[0]","lambda:1","open('file')","sin(x,y)","log(x,base=2)","x**x","x**13","True","1e999","().__class__"]:
            with self.subTest(expression=expression), self.assertRaises((ValueError,SyntaxError)):
                validate_math({"operation":"simplify","expression":expression})
        self.assertEqual(validate_math({"operation":"factor","expression":"x**2-1"})["expression"],"x**2-1")

    def test_fixed_optimization_shapes(self):
        invalid=[{"operation":"assignment","costs":[[1,2]]}, {"operation":"assignment","costs":[[True]]},
                 {"operation":"assignment","costs":[[1]],"sql":"x"}, {"operation":"routing","distance_matrix":[[1,2],[2,0]]},
                 {"operation":"routing","distance_matrix":[[0,1],[1,0]],"vehicles":20},
                 {"operation":"packing","weights":[3],"capacity":2}, {"operation":"schedule","durations":[1],"precedence":[[0,0]]},
                 {"operation":"schedule","durations":[1],"machines":[0,1]}, {"operation":"schedule","durations":[1],"seconds":True}]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError): validate_optimization(args)

    def test_office_formats_cannot_import_macros_links_or_formulas(self):
        with self.assertRaises(ValueError): validate_args("office_create",{"format":"docx","path":"x.docx","title":"x","paragraphs":["x"],"macro":"evil"})
        raw=office_bytes(validate_args("office_create",{"format":"xlsx","path":"x.xlsx","title":"x","rows":[["=WEBSERVICE(\"https://evil\")","<x>&"]]}))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist(): ET.fromstring(archive.read(name))
            sheet=archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('t="inlineStr"',sheet); self.assertIn('=WEBSERVICE',sheet)
            self.assertNotIn('<f>',sheet); self.assertNotIn('TargetMode="External"',''.join(archive.read(n).decode() for n in archive.namelist()))
            self.assertNotIn('vbaProject.bin',archive.namelist())

    def test_geojson_rejects_uri_projects_nested_properties_and_invalid_coords(self):
        for data in [{"type":"FeatureCollection","features":[],"url":"file:///secret"},
                     {"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[190,1]}}]},
                     {"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[1,2]},"properties":{"x":{"url":"x"}}}]}]:
            with self.subTest(data=data), self.assertRaises(ValueError): geojson(json.dumps(data).encode())
        with self.assertRaises(ValueError): geojson(b'{"type":"FeatureCollection","features":[],"features":[]}')

    def test_ffmpeg_refuses_playlist_before_native_execution(self):
        with tempfile.TemporaryDirectory(prefix='.production-media-',dir=Path.cwd()) as temporary, mock.patch('ariadne._production_worker.native_run') as run:
            with self.assertRaises(ValueError): native_operation('media_transform',{'operation':'audio_wav','start':0,'duration':1},{},Path(temporary),b'#EXTM3U\nfile:///secrets')
            run.assert_not_called()

    def test_spreadsheet_formulas_are_finite_local_numeric_input_ranges(self):
        for rows in [[[{"sum":"A1"}]], [[1],[{'sum':'A1:A2'}]], [[{'sum':"'[https://evil/x.xlsx]Sheet'!A1"}]], [[{'number':'NaN'}]], [[{'number':'1e3'}]], [[{'number':'0.1234567890123456'}]], [[{'sum_product':['A1:A2','A1:B1']}]], [[{'formula':'WEBSERVICE("x")'}]], [[True]]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError): spreadsheet_cells(rows)
        clean,cache=spreadsheet_cells([[2,{'number':'1.25'}],[3,{'number':'2.5'}],[{'sum':'A1:A2'},{'sum_product':['A1:A2','B1:B2']}]])
        self.assertEqual(cache,{(3,1):'5',(3,2):'10'})


class ProductionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='.production-test-',dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.registry=ToolRegistry(Settings(data_dir=Path(self.temporary.name)))
        self.tools=ProductionTools(self.registry)

    def execute(self,name,args):
        result=self.tools.execute(name,args)
        self.assertTrue(result.get('ok'),result)
        return result

    def test_office_all_three_real_worker_artifacts_and_exclusive_commit(self):
        for fmt,content in [('docx',{'paragraphs':['A report & <data>']}),('xlsx',{'rows':[['ID','Value'],['001','=1+2']]}),('pptx',{'slides':[{'title':'Offline','body':'Power independent\nLocal content'}]})]:
            args={'format':fmt,'path':'reports/output.'+fmt,'title':'Ariadne',**content}
            result=self.execute('office_create',args); path=self.registry.root/result['artifact']['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),result['artifact']['sha256'])
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                for name in archive.namelist(): ET.fromstring(archive.read(name))
            original=path.read_bytes(); self.assertFalse(self.tools.execute('office_create',args)['ok']); self.assertEqual(path.read_bytes(),original)
            self.assertEqual(self.tools.policy('office_create',args),'approval')

    def test_output_path_escape_and_cancellation_do_not_write(self):
        base={'format':'docx','title':'Ariadne','paragraphs':['x']}
        for path in ['../escape.docx','E:\\escape.docx','/escape.docx']:
            self.assertFalse(self.tools.execute('office_create',{**base,'path':path})['ok'])
        result=self.tools.execute('office_create',{**base,'path':'cancelled.docx'},cancelled=lambda:True)
        self.assertFalse(result['ok']); self.assertFalse((self.registry.root/'cancelled.docx').exists())

    def test_missing_engines_are_not_advertised(self):
        with mock.patch('ariadne.production_tools.installation',return_value={'packages':{},'binaries':{}}):
            names={schema['function']['name'] for schema in self.tools.schemas()}
            self.assertEqual(names,{'office_create','geo_analyze'})
            self.assertFalse(self.tools.status()['features']['optimization'])
            self.assertFalse(self.tools.execute('production_math',{'operation':'factor','expression':'x**2-1'})['ok'])
            self.assertFalse(self.tools.execute('office_create',{'format':'pdf','path':'x.pdf','title':'x','paragraphs':['x']})['ok'])

    def test_geojson_worker_provenance_and_full_feature_bbox_semantics(self):
        data={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[-87,41]},'properties':{'name':'A'}},{'type':'Feature','geometry':{'type':'LineString','coordinates':[[-88,40],[-86,42]]},'properties':{}}]}
        path=self.registry.root/'points.geojson'; path.write_text(json.dumps(data),encoding='utf-8')
        result=self.execute('geo_analyze',{'path':'points.geojson','operation':'filter_bbox','bbox':[-87.5,40.5,-86.5,41.5]})
        self.assertEqual(result['features'],2); self.assertEqual(result['selected_features'],1); self.assertEqual(result['bbox'],[-88,40,-86,42])
        self.assertEqual(result['source']['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipUnless('ortools' in installation()['packages'],'Pinned isolated OR-Tools not installed')
    def test_assignment_matches_independent_permutation_oracle(self):
        costs=[[9,2,7],[6,4,3],[5,8,1],[7,6,9]]
        optimum=min(sum(costs[worker][task] for task,worker in enumerate(workers)) for workers in itertools.permutations(range(len(costs)),len(costs[0])))
        result=self.execute('production_optimize',{'operation':'assignment','costs':costs})
        self.assertTrue(result['optimal']); self.assertEqual(result['objective'],optimum)
        self.assertEqual(len({a['worker'] for a in result['assignments']}),3)
        self.assertEqual({a['task'] for a in result['assignments']},{0,1,2})

    @unittest.skipUnless('ortools' in installation()['packages'],'Pinned isolated OR-Tools not installed')
    def test_schedule_packing_and_route_have_valid_constraints_and_objectives(self):
        schedule=self.execute('production_optimize',{'operation':'schedule','durations':[3,2,4],'machines':[0,1,0],'precedence':[[0,1]]})
        tasks=schedule['tasks']; self.assertEqual(schedule['objective'],7); self.assertLessEqual(tasks[0]['end'],tasks[1]['start'])
        self.assertTrue(tasks[0]['end']<=tasks[2]['start'] or tasks[2]['end']<=tasks[0]['start'])
        packing=self.execute('production_optimize',{'operation':'packing','weights':[4,4,2,2],'capacity':6})
        self.assertTrue(packing['optimal']); self.assertEqual(packing['objective'],2)
        self.assertEqual(sorted(i for bucket in packing['bins'] for i in bucket['items']),[0,1,2,3])
        self.assertTrue(all(bucket['weight']<=6 for bucket in packing['bins']))
        matrix=[[0,2,9,3],[2,0,4,8],[9,4,0,2],[3,8,2,0]]
        route=self.execute('production_optimize',{'operation':'routing','distance_matrix':matrix,'seconds':1})
        self.assertFalse(route['optimal']); self.assertEqual(sorted(route['routes'][0][1:-1]),[1,2,3])
        self.assertEqual(route['objective'],sum(matrix[a][b] for r in route['routes'] for a,b in zip(r,r[1:])))

    @unittest.skipUnless('sympy' in installation()['packages'],'Pinned isolated SymPy not installed')
    def test_real_symbolic_derivative_roots_and_exact_decimal_substitution(self):
        derivative=self.execute('production_math',{'operation':'differentiate','expression':'x**3+sin(x)'})
        self.assertEqual(derivative['expression'],'3*x**2 + cos(x)')
        roots=self.execute('production_math',{'operation':'solve','expression':'x**2-4'})
        self.assertEqual(roots['solution_set'],'{-2, 2}'); self.assertTrue(roots['complete'])
        result=self.execute('production_math',{'operation':'evaluate','expression':'x+0.2','values':{'x':0.1}})
        self.assertEqual(result['exact'],'3/10')
        invalid=self.tools.execute('production_math',{'operation':'evaluate','expression':'1/x','values':{'x':0}})
        self.assertFalse(invalid['ok'])

    @unittest.skipUnless('ffmpeg' in installation()['binaries'],'Pinned local FFmpeg not installed')
    def test_real_ffmpeg_audio_conversion_and_checked_snapshot_survives_edit(self):
        memory=io.BytesIO()
        with wave.open(memory,'wb') as source:
            source.setnchannels(2); source.setsampwidth(2); source.setframerate(8000); source.writeframes(b'\0\0\0\0'*8000)
        path=self.registry.root/'input.wav'; path.write_bytes(memory.getvalue())
        args={'path':'input.wav','output':'mono.wav','operation':'audio_wav','duration':1}
        fingerprint=self.tools.fingerprint_material('media_transform',args)
        approved=fingerprint['source']['sha256']; path.write_bytes(b'changed after final approval check')
        result=self.execute('media_transform',args)
        self.assertEqual(result['source']['sha256'],approved)
        with wave.open(str(self.registry.root/'mono.wav'),'rb') as generated:
            self.assertEqual(generated.getnchannels(),1); self.assertEqual(generated.getframerate(),16000); self.assertGreater(generated.getnframes(),15000)

    @unittest.skipUnless('ffmpeg' in installation()['binaries'],'Pinned local FFmpeg not installed')
    def test_real_ffmpeg_thumbnail_and_short_mp4(self):
        engine=installation()['binaries']['ffmpeg']['path']
        source=self.registry.root/'video.mp4'
        subprocess.run([engine,'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-threads','2',str(source)],check=True,timeout=30)
        thumbnail=self.execute('media_transform',{'path':'video.mp4','output':'thumbnail.png','operation':'thumbnail','duration':1})
        self.assertTrue((self.registry.root/'thumbnail.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(thumbnail['artifact']['bytes'],100)
        self.execute('media_transform',{'path':'video.mp4','output':'clip.mp4','operation':'clip_mp4','duration':.5})
        self.assertEqual((self.registry.root/'clip.mp4').read_bytes()[4:8],b'ftyp')

    @unittest.skipUnless('freecad' in installation()['binaries'],'Pinned local FreeCAD not installed')
    def test_real_freecad_plate_step_export(self):
        result=self.execute('cad_part',{'shape':'plate_with_holes','dimensions':{'length':60,'width':40,'height':4},'format':'step','path':'plate.step'})
        self.assertTrue((self.registry.root/'plate.step').read_text(encoding='utf-8').startswith('ISO-10303-21'))
        self.assertGreater(result['artifact']['bytes'],1000)

    @unittest.skipUnless('libreoffice' in installation()['binaries'],'Pinned local LibreOffice not installed')
    def test_real_libreoffice_generated_report_to_pdf(self):
        result=self.execute('office_create',{'format':'pdf','path':'report.pdf','title':'Offline report','paragraphs':['Ariadne local conversion.']})
        self.assertTrue((self.registry.root/'report.pdf').read_bytes().startswith(b'%PDF-'))
        self.assertEqual(result['engine'],'libreoffice')

    @unittest.skipUnless('blender' in installation()['binaries'],'Pinned local Blender not installed')
    def test_real_blender_cpu_product_render_has_visible_pixels(self):
        result=self.execute('render_product',{'shape':'box','dimensions':[2,1,1],'color':[.1,.5,.2],'size':128,'path':'product.png'})
        raw=(self.registry.root/'product.png').read_bytes()
        self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as generated:
            self.assertEqual(generated.size,(128,128))
            self.assertTrue(any(high-low>10 for low,high in generated.convert('RGB').getextrema()))
        if result['engine']=='blender_bpy': self.assertEqual(result['device'],'CPU')

    @unittest.skipUnless('qgis' in installation()['binaries'],'Pinned local QGIS not installed')
    def test_real_qgis_geographic_buffer(self):
        source={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[-87,41]},'properties':{'name':'site'}}]}
        (self.registry.root/'site.geojson').write_text(json.dumps(source),encoding='utf-8')
        result=self.execute('gis_buffer',{'path':'site.geojson','output':'buffer.geojson','distance_degrees':.01})
        generated=json.loads((self.registry.root/'buffer.geojson').read_text(encoding='utf-8'))
        geometry=generated['features'][0]['geometry']
        self.assertIn(geometry['type'],{'Polygon','MultiPolygon'})
        points=geometry['coordinates'][0] if geometry['type']=='Polygon' else geometry['coordinates'][0][0]
        self.assertAlmostEqual(min(p[0] for p in points),-87.01,places=5)
        self.assertAlmostEqual(max(p[1] for p in points),41.01,places=5)
        self.assertEqual(result['engine'],'qgis')

    @unittest.skipUnless('kicad' in installation()['binaries'],'Pinned local KiCad not installed')
    def test_real_kicad_drc_and_gerber_package(self):
        result=self.execute('pcb_manufacture',{'width':40,'height':30,'path':'board.zip'})
        with zipfile.ZipFile(self.registry.root/'board.zip') as archive:
            self.assertIn('board.kicad_pcb',archive.namelist()); self.assertIn('drc.json',archive.namelist())
            report=json.loads(archive.read('drc.json'))
            self.assertIsInstance(report,dict)
            gerbers=[name for name in archive.namelist() if name.startswith('gerbers/')]
            self.assertGreaterEqual(len(gerbers),5)
            self.assertTrue(any(b'%FS' in archive.read(name) for name in gerbers))
            self.assertIn('(gr_rect',archive.read('board.kicad_pcb').decode())
        self.assertEqual(result['engine'],'kicad')

    @unittest.skipUnless('libreoffice' in installation()['binaries'],'Pinned local LibreOffice not installed')
    def test_real_libreoffice_numeric_workbook_recalculates_fixed_formulas(self):
        rows=[['Item','Qty','Price','Value'],['A',2,{'number':'1.25'}],['B',3,{'number':'2.50'}],['Total',{'sum':'B2:B3'},{'sum':'C2:C3'},{'sum_product':['B2:B3','C2:C3']}]]
        result=self.execute('office_create',{'format':'xlsx','path':'budget.xlsx','title':'Budget','rows':rows})
        self.assertTrue(result['recalculated']); self.assertTrue(result['formulas'])
        with zipfile.ZipFile(self.registry.root/'budget.xlsx') as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        cells={cell.attrib['r']:cell for cell in sheet.findall('.//s:c',ns)}
        self.assertEqual(cells['B4'].find('s:v',ns).text,'5')
        self.assertEqual(cells['D4'].find('s:v',ns).text,'10')
        self.assertIn('SUMPRODUCT',cells['D4'].find('s:f',ns).text)

    @unittest.skipUnless('libreoffice' in installation()['binaries'],'Pinned local LibreOffice not installed')
    def test_real_libreoffice_opens_generated_spreadsheet_and_slides(self):
        # Independent native importer/rendering check, beyond ZIP/XML validity.
        # All source content is generated by this fixture, with a private profile.
        from ariadne._production_worker import native_run
        from pypdf import PdfReader
        engine=installation()['binaries']['libreoffice']
        for fmt,contents,marker in [('xlsx',{'rows':[['Evidence','17']]},'Evidence'),('pptx',{'slides':[{'title':'Visible Evidence','body':'Offline slide validation'}]},'Visible Evidence')]:
            args={'format':fmt,'path':'source.'+fmt,'title':'Ariadne',**contents}
            source=self.registry.root/args['path']; source.write_bytes(office_bytes(validate_args('office_create',args)))
            stage=self.registry.root/('convert-'+fmt); stage.mkdir()
            native_run(engine,['-env:UserInstallation='+(stage/'profile').as_uri(),'--headless','--norestore','--nodefault','--nolockcheck','--convert-to','pdf','--outdir',str(stage),str(source)],stage)
            pdf=stage/'source.pdf'; self.assertTrue(pdf.exists())
            self.assertIn(marker,'\n'.join(page.extract_text() for page in PdfReader(pdf).pages))


if __name__=='__main__': unittest.main()
