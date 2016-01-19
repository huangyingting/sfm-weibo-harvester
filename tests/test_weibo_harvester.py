import tests
from tests.weibos import weibo1
import unittest
from mock import MagicMock, patch
from kombu import Connection, Exchange, Queue, Producer
from sfmutils.state_store import DictHarvestStateStore
from sfmutils.harvester import HarvestResult, EXCHANGE
import threading
import shutil
import tempfile
import time
from datetime import datetime
from weibo_harvester import WeiboHarvester
from weibowarc import Weibowarc


class TestWeiboHarvester(tests.TestCase):
    def setUp(self):
        self.harvester = WeiboHarvester()
        self.harvester.state_store = DictHarvestStateStore()
        self.harvester.harvest_result = HarvestResult()
        self.harvester.stop_event = threading.Event()
        self.harvester.harvest_result_lock = threading.Lock()
        self.harvester.message = {
            "id": "test:1",
            "type": "weibo_timeline",
            "credentials": {
                "api_key": tests.WEIBO_API_KEY,
                "api_secret": tests.WEIBO_API_SECRET,
                "redirect_uri": tests.WEIBO_REDIRECT_URI,
                "access_token": tests.WEIBO_ACCESS_TOKEN
            },
            "collection": {
                "id": "test_collection",
                "path": "/collections/test_collection"
            }
        }

    @patch("weibo_harvester.Weibowarc", autospec=True)
    def test_search_timeline(self, mock_weibowarc_class):

        mock_weibowarc = MagicMock(spec=Weibowarc)
        # Expecting 2 results. First returns 1tweets. Second returns none.
        mock_weibowarc.search_friendships.side_effect = [(weibo1), ()]
        # Return mock_weibowarc when instantiating a weibowarc.
        mock_weibowarc_class.side_effect = [mock_weibowarc]

        self.assertDictEqual({"weibo": 1}, self.harvester.harvest_result.summary)
        mock_weibowarc_class.assert_called_once_with(tests.WEIBO_API_KEY, tests.WEIBO_API_SECRET,
                                                     tests.WEIBO_REDIRECT_URI, tests.WEIBO_ACCESS_TOKEN)


@unittest.skipIf(not tests.test_config_available, "Skipping test since test config not available.")
@unittest.skipIf(not tests.integration_env_available, "Skipping test since integration env not available.")
class TestWeiboHarvesterIntegration(tests.TestCase):
    def _create_connection(self):
        return Connection(hostname="mq", userid=tests.mq_username, password=tests.mq_password)

    def setUp(self):
        self.exchange = Exchange(EXCHANGE, type="topic")
        self.result_queue = Queue(name="result_queue", routing_key="harvest.status.weibo.*", exchange=self.exchange,
                                  durable=True)
        self.web_harvest_queue = Queue(name="web_harvest_queue", routing_key="harvest.start.web", exchange=self.exchange)
        self.warc_created_queue = Queue(name="warc_created_queue", routing_key="warc_created", exchange=self.exchange)
        weibo_harvester_queue = Queue(name="weibo_harvester", exchange=self.exchange)
        with self._create_connection() as connection:
            self.result_queue(connection).declare()
            self.result_queue(connection).purge()
            self.web_harvest_queue(connection).declare()
            self.web_harvest_queue(connection).purge()
            self.warc_created_queue(connection).declare()
            self.warc_created_queue(connection).purge()
            weibo_harvester_queue(connection).purge()

        self.collection_path = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.collection_path, ignore_errors=True)

    def test_search(self):
        harvest_msg = {
            "id": "test:1",
            "type": "weibo_timeline",
            "credentials": {
                "api_key": tests.WEIBO_API_KEY,
                "api_secret": tests.WEIBO_API_SECRET,
                "redirect_uri": tests.WEIBO_REDIRECT_URI,
                "access_token": tests.WEIBO_ACCESS_TOKEN
            },
            "collection": {
                "id": "test_collection",
                "path": self.collection_path

            }
        }
        with self._create_connection() as connection:
            bound_exchange = self.exchange(connection)
            producer = Producer(connection, exchange=bound_exchange)
            producer.publish(harvest_msg, routing_key="harvest.start.weibo.weibo_timeline")

            # Now wait for result message.
            counter = 0
            bound_result_queue = self.result_queue(connection)
            message_obj = None
            while counter < 240 and not message_obj:
                time.sleep(.5)
                message_obj = bound_result_queue.get(no_ack=True)
                counter += 1
            self.assertTrue(message_obj, "Timed out waiting for result at {}.".format(datetime.now()))
            result_msg = message_obj.payload
            # Matching ids
            self.assertEqual("test:1", result_msg["id"])
            # Success
            self.assertEqual("completed success", result_msg["status"])
            # Some weibo posts
            self.assertTrue(result_msg["summary"]["weibo"])

            # Web harvest message.
            bound_web_harvest_queue = self.web_harvest_queue(connection)
            message_obj = bound_web_harvest_queue.get(no_ack=True)
            self.assertIsNotNone(message_obj, "No web harvest message.")

            # Warc created message.
            bound_warc_created_queue = self.warc_created_queue(connection)
            message_obj = bound_warc_created_queue.get(no_ack=True)
            self.assertIsNotNone(message_obj, "No warc created message.")