import cp
import pytz
import copy
import json
import flask
import unittest
import superdesk
import requests_mock
import settings

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from superdesk.metadata.item import SCHEDULE_SETTINGS, PUB_STATUS
from tests.ingest.parser import get_fixture_path

from tests.mock import resources

from cp.ingest import CP_APMediaFeedParser
from cp.ingest.parser.ap import CATEGORY_SCHEME


with open(get_fixture_path("item.json", "ap")) as fp:
    data = json.load(fp)

with open(get_fixture_path("picture.json", "ap")) as fp:
    picture_data = json.load(fp)

provider = {}
parser = CP_APMediaFeedParser()


class CP_AP_ParseTestCase(unittest.TestCase):

    app = flask.Flask(__name__)
    app.locators = MagicMock()
    app.config.update({"AP_TAGS_MAPPING": settings.AP_TAGS_MAPPING})

    def test_slugline(self):
        parser = CP_APMediaFeedParser()
        self.assertEqual("foo-bar-baz", parser.process_slugline("foo bar/baz"))
        self.assertEqual("foo-bar", parser.process_slugline("foo-bar"))
        self.assertEqual("foo-bar", parser.process_slugline("foo - bar"))

    def test_parse(self):
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                item = parser.parse(data, provider)

        self.assertEqual("ba7d03f0cd24a17faa81bebc724bcf3f", item["guid"])
        self.assertEqual("Story", item["profile"])
        self.assertEqual("WY-Exchange-Coronavirus-Tech", item["slugline"])
        self.assertEqual("headline1", item["headline"])
        self.assertEqual("headline1", item["extra"][cp.HEADLINE2])
        self.assertIn("copyright information", item["copyrightnotice"])
        self.assertIn("editorial use only", item["usageterms"])
        self.assertEqual("The Associated Press", item["source"])
        self.assertEqual(5, item["urgency"])
        self.assertEqual("Margaret Austin", item["byline"])
        self.assertIn("General news", item["keywords"])

        self.assertIn(
            {
                "name": "Feature",
                "qcode": "Feature",
            },
            item["genre"],
        )

        self.assertEqual("UPDATES: With AP Photos.", item["extra"]["update"])
        self.assertEqual("", item["ednote"])

        self.assertEqual("NYSE:WFC", item["extra"]["stocks"])
        self.assertEqual("m0012", item["extra"][cp.FILENAME])
        self.assertEqual(0, item["extra"]["ap_version"])

        self.assertIn(
            {
                "name": "International",
                "qcode": "w",
                "scheme": "categories",
            },
            item["anpa_category"],
        )

        subjects = [
            s["name"] for s in item["subject"] if s.get("scheme") == "subject_custom"
        ]
        self.assertIn("science and technology", subjects)
        self.assertIn("health", subjects)
        self.assertIn("mass media", subjects)
        self.assertIn("technology and engineering", subjects)

        tags = [s["name"] for s in item["subject"] if s.get("scheme") == cp.TAG]
        self.assertEqual(2, len(tags))
        self.assertIn("APV", tags)
        self.assertIn("TSX", tags)

        products = [s["qcode"] for s in item["subject"] if s.get("scheme") == cp.AP_PRODUCT]
        self.assertEqual(6, len(products))
        self.assertIn("33381", products)

        dateline = item["dateline"]
        self.assertEqual("Wyoming Tribune Eagle", dateline["source"])
        self.assertEqual("CHEYENNE, Wyo.", dateline["text"])
        self.assertIn("located", dateline)
        self.assertEqual("Cheyenne", dateline["located"]["city"])
        self.assertEqual("Wyoming", dateline["located"]["state"])
        self.assertEqual("WY", dateline["located"]["state_code"])
        self.assertEqual("United States", dateline["located"]["country"])
        self.assertEqual("USA", dateline["located"]["country_code"])
        self.assertEqual(41.13998, dateline["located"]["location"]["lat"])
        self.assertEqual(-104.82025, dateline["located"]["location"]["lon"])

        self.assertIn("associations", item)
        self.assertIn("media-gallery--1", item["associations"])
        self.assertIn("media-gallery--2", item["associations"])

        self.assertEqual(1, len(item["place"]))
        self.assertEqual(
            {
                "name": "Cheyenne",
                "qcode": "Cheyenne",
                "state": "Wyoming",
                "country": "United States",
                "world_region": "North America",
                "location": {
                    "lat": 41.13998,
                    "lon": -104.82025,
                },
            },
            item["place"][0],
        )

        self.assertRegex(item["body_html"], r"^<p>.*</p>$")

    def test_parse_ignore_associations_based_on_type_config(self):
        _provider = {
            "content_types": ["text"],
        }

        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                item = parser.parse(data, _provider)

        self.assertFalse(item.get("associations"))

    def test_parse_picture(self):
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                with requests_mock.mock() as mock:
                    with open(get_fixture_path("preview.jpg", "ap"), "rb") as f:
                        mock.get(
                            picture_data["data"]["item"]["renditions"]["preview"][
                                "href"
                            ],
                            content=f.read(),
                        )
                    item = parser.parse(picture_data, provider)

        self.assertEqual("Jae C. Hong", item["byline"])
        self.assertEqual(5, item["urgency"])
        self.assertEqual("ASSOCIATED PRESS", item["creditline"])
        self.assertEqual("America Protests Racial Economics", item["headline"])
        self.assertEqual("stf", item["extra"]["photographer_code"])
        self.assertIn("Pedestrians are silhouetted", item["description_text"])
        self.assertEqual("AP", item["extra"]["provider"])

    def test_parse_embargoed(self):
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                source = copy.deepcopy(data)
                embargoed = datetime.now(pytz.utc).replace(microsecond=0) + timedelta(
                    hours=2
                )
                source["data"]["item"]["embargoed"] = embargoed.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                source["data"]["item"]["pubstatus"] = "embargoed"
                item = parser.parse(source, provider)
                self.assertEqual(embargoed, item["embargoed"])
                self.assertIn("embargo", item)
                self.assertEqual(
                    {
                        "utc_embargo": embargoed,
                        "time_zone": cp.TZ,
                    },
                    item[SCHEDULE_SETTINGS],
                )
                self.assertEqual(PUB_STATUS.HOLD, item["pubstatus"])
                self.assertEqual(
                    ["Advance"], [genre["name"] for genre in item["genre"]]
                )

                embargoed = embargoed - timedelta(hours=5)
                source["data"]["item"]["embargoed"] = embargoed.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                item = parser.parse(source, provider)
                self.assertEqual(embargoed, item["embargoed"])
                self.assertNotIn("embargo", item)

    def test_category_politics_international(self):
        with open(get_fixture_path("politics.json", "ap")) as fp:
            _data = json.load(fp)
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                item = parser.parse(_data, {})
        self.assertEqual(
            [
                {
                    "name": "International",
                    "qcode": "w",
                    "scheme": CATEGORY_SCHEME,
                }
            ],
            item["anpa_category"],
        )
        self.assertEqual("US-Biden-Staff", item["slugline"])

    def test_category_apv(self):
        with open(get_fixture_path("apv.json", "ap")) as fp:
            _data = json.load(fp)
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                item = parser.parse(_data, {})
        self.assertEqual(
            [
                {
                    "name": "International",
                    "qcode": "w",
                    "scheme": CATEGORY_SCHEME,
                }
            ],
            item["anpa_category"],
        )
        self.assertEqual("EU-Spain-Storm-Aftermath", item["slugline"])

    def test_category_tenis(self):
        with open(get_fixture_path("ap-sports.json", "ap")) as fp:
            _data = json.load(fp)
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                item = parser.parse(_data, {})
        self.assertEqual(
            [
                {
                    "name": "Agate",
                    "qcode": "r",
                    "scheme": CATEGORY_SCHEME,
                }
            ],
            item["anpa_category"],
        )

    def test_slugline_prev_version(self):
        with open(get_fixture_path("ap-sports.json", "ap")) as fp:
            _data = json.load(fp)
        with self.app.app_context():
            with patch.dict(superdesk.resources, resources):
                resources["ingest"].service.find_one.return_value = {
                    "slugline": "prev-slugline",
                }
                item = parser.parse(_data, {})
                resources["ingest"].service.find_one.return_value = None
        self.assertEqual("prev-slugline", item["slugline"])
