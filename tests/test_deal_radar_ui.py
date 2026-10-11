"""Real Streamlit UI tests against an isolated runtime. All leads are synthetic."""
import json
import os
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock
from pathlib import Path
from unittest.mock import patch
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from app.deal_radar_ui import filter_leads, safe_url, vacancy, income_fact, lead_identity
from app import observability, data_access, deal_radar_ui

LEADS=[
 dict(id='a',title='Synthetic 75% center',url='https://example.com/a',city='Alpha',state='TX',asking_price=2500000,occupancy_pct=75,gla_sf=20000,broker_noi=None,cap_rate=None,source_name='Broker',source_status='broker_assertion',retrieved_at='2026-10-11',listing_date=None,active_status='unknown',property_type='Open-air strip center',is_demo=True),
 dict(id='b',title='<script>synthetic unsafe title</script>',url='javascript:alert(1)',city='Beta',state='FL',asking_price=None,occupancy_pct=None,gla_sf=None,source_name='LoopNet',source_status='discovery_only',is_demo=True),
 dict(id='c',title='Synthetic high vacancy',url='https://example.com/c',city='Alpha',state='TX',asking_price=8000000,occupancy_pct=45,gla_sf=30000,source_name='Crexi',source_status='broker_assertion',is_demo=True),
 dict(id='d',title='Synthetic outside buy box',url='https://example.com/d',city='Beta',state='FL',asking_price=12000000,occupancy_pct=75,source_name='Broker',source_status='broker_assertion',is_demo=True),
]

class Filters(unittest.TestCase):
 def test_confirmed_buybox_and_unknown_queue(self):
  self.assertEqual([x['id'] for x in filter_leads(LEADS)],['a','b','c'])
  self.assertEqual([x['id'] for x in filter_leads(LEADS,include_unknown=False)],['a','c'])
  self.assertIsNone(vacancy(LEADS[1]))
  self.assertEqual(len(filter_leads([dict(LEADS[0],source_name=None)],sources=["Other / broker"])),1)
 def test_directories_and_reports_are_not_property_candidates(self):
  resources=[dict(LEADS[0],id="directory",lead_kind="listing_directory"),dict(LEADS[0],id="report",lead_kind="market_report")]
  self.assertEqual([x["id"] for x in filter_leads(resources+LEADS)], ["a","b","c"])
 def test_high_vacancy_and_market(self):
  self.assertEqual([x['id'] for x in filter_leads(LEADS,vacancy_band=(35,60),include_unknown=False)],['c'])
  self.assertEqual([x['id'] for x in filter_leads(LEADS,market='Beta, FL')],['b'])
 def test_income_requires_period_geography_and_source(self):
  row=dict(LEADS[0],median_household_income=120000)
  self.assertIsNone(income_fact(row))
  self.assertEqual(filter_leads([row],minimum_income=100000),[])
  row.update(income_geography='Synthetic county',income_period='2020–2024',income_source_url='https://example.com/income')
  self.assertEqual(income_fact(row),120000)
 def test_unsafe_links_and_stable_identity(self):
  for url in ['javascript:alert(1)','https://user:password@example.com','http://localhost:8502','http://127.0.0.1','https://example.com\nsecret','https://example.com/public?access_token=synthetic']:
   self.assertIsNone(safe_url(url))
  self.assertEqual(safe_url('https://example.com/public'),'https://example.com/public')
  a={'title':'A','url':'https://example.com/a'};b={'title':'B','url':'https://example.com/b'}
  self.assertNotEqual(lead_identity(a),lead_identity(b))

class RealUI(unittest.TestCase):
 def setUp(self):
  self.patches=[patch.object(deal_radar_ui,"load_leads",return_value=([],None)),patch.dict(os.environ,{},clear=True),patch.object(st,'secrets',{}),patch.object(requests.sessions.Session,'request',side_effect=AssertionError('Network forbidden')),patch.object(observability,'init_sentry',return_value=False),patch.object(data_access,'get_snowflake_config',return_value={}),patch.object(data_access,'get_snowpark_session',side_effect=RuntimeError('Synthetic offline absence'))]
  for p in self.patches:p.start()
  st.cache_data.clear()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
 def app(self,entry,leads=None):
  app=AppTest.from_file(str(ROOT/entry))
  if leads is not None:app.session_state['listing_leads']=leads
  return app.run(timeout=20)
 def test_both_default_discovery_routes_do_not_load_dataset(self):
  for entry in ['streamlit_app.py','snowflake_streamlit_app.py']:
   with self.subTest(entry=entry):
    app=self.app(entry)
    self.assertEqual(len(app.exception),0)
    self.assertEqual(app.radio[0].value,'Find deals')
    self.assertEqual(len(app.chat_input),0)
    self.assertTrue(any('No public leads are loaded yet' in x.value for x in app.markdown))
    self.assertFalse(any('Illustrative Value'==x.label for x in app.metric))
 def test_selection_escaping_unknowns_and_no_automatic_research(self):
  app=self.app('snowflake_streamlit_app.py',LEADS)
  self.assertEqual(len(app.exception),0)
  html='\n'.join(x.value for x in app.markdown)
  self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>synthetic unsafe title',html)
  self.assertIn('Reported occupancy',html);self.assertIn('Unknown',html)
  self.assertIn('&#36;2.50m',html)  # Currency in HTML must not become cross-tag Markdown math.
  self.assertNotIn('Synthetic outside buy box',html)
  [x for x in app.button if x.label=='Review this lead'][0].click().run()
  self.assertEqual(len(app.exception),0)
  self.assertEqual(app.session_state['radar_selected_id'],'a')
  self.assertTrue(any('IRR and equity-return fit have not been calculated' in x.value for x in app.info))
  self.assertTrue(any('What would disprove the thesis' in x.value for x in app.markdown))
 def test_filters_hide_unknowns_and_keep_separate_listing_workspace(self):
  app=self.app('snowflake_streamlit_app.py',LEADS)
  app.checkbox[0].uncheck().run()
  self.assertEqual(len([x for x in app.button if x.label=='Review this lead']),2)
  next(x for x in app.selectbox if x.label=='Reported vacancy').set_value('35–60% vacancy').run()
  self.assertEqual(len([x for x in app.button if x.label=='Review this lead']),1)
  next(x for x in app.selectbox if x.label=='Median household income').set_value('Published $100k+').run()
  self.assertEqual(len([x for x in app.button if x.label=='Review this lead']),0)
 def test_source_hypotheses_and_resource_separation(self):
  row=dict(LEADS[0],why_investigate="Synthetic broker lease-up hypothesis",vacancy_drivers=["Synthetic achievable-rent question"],source_support_location="Synthetic page 2",missing_fields=["T12"],counterevidence=["Synthetic weak inquiries"] )
  directory=dict(LEADS[0],id="directory",title="Synthetic directory",lead_kind="listing_directory")
  app=self.app("snowflake_streamlit_app.py",[row,directory])
  self.assertEqual(len([x for x in app.button if x.label=="Review this lead"]),1)
  next(x for x in app.button if x.label=="Review this lead").click().run()
  text="\n".join(str(x.value) for kind in (app.text, app.markdown, app.caption) for x in kind)
  self.assertIn("Synthetic achievable-rent question",text)
  self.assertIn("Synthetic broker lease-up hypothesis",text)
  self.assertIn("Synthetic weak inquiries",text)
  self.assertTrue(any("not property candidates" in x.value for x in app.caption))
 def test_blank_model_cannot_contact_cortex(self):
  app=self.app("snowflake_streamlit_app.py")
  app.radio[0].set_value("Center analysis").run(timeout=20)
  self.assertTrue(any("Hackathon model not configured" in x.value for x in app.caption))
  context=ModuleType("snowflake.snowpark.context")
  context.get_active_session=Mock(side_effect=AssertionError("No model session allowed"))
  modules={"snowflake":ModuleType("snowflake"),"snowflake.snowpark":ModuleType("snowflake.snowpark"),"snowflake.snowpark.context":context}
  with patch.dict(sys.modules,modules):
   app.chat_input[0].set_value("Should we buy?").run(timeout=20)
  context.get_active_session.assert_not_called()
  self.assertEqual(len(app.exception),0)
  self.assertTrue(any("hackathon model not configured" in x.value for x in app.markdown))
 def test_explicit_search_without_binding_is_inert(self):
  app=self.app('snowflake_streamlit_app.py')
  next(x for x in app.button if x.label=='Search current listings').click().run()
  self.assertEqual(len(app.exception),0)
  self.assertTrue(any('not configured' in x.value for x in app.info))
 def test_center_analysis_preserves_chat_and_tabs(self):
  for entry in ['streamlit_app.py','snowflake_streamlit_app.py']:
   with self.subTest(entry=entry):
    app=self.app(entry)
    app.radio[0].set_value('Center analysis').run(timeout=20)
    self.assertEqual(len(app.exception),0)
    self.assertEqual(len(app.chat_input),1)
    self.assertEqual([x.label for x in app.tabs],['Model','Rent Roll','Market','Comps'])
    self.assertTrue(any('DEMO MODEL' in x.value for x in app.markdown))
    self.assertEqual(len([x for x in app.metric if x.label in ['Illustrative Value','Illustrative NOI','Heuristic Rent','Value / SF']]),4)
    self.assertTrue(any(x.label=='Evidence gaps and model diagnostics' for x in app.expander))

if __name__=='__main__':unittest.main(verbosity=2)
