#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
import tests
import vcr as base_vcr
from tests.weibos import weibo1, weibo2, weibo3, weibo4, weibo5
import unittest
from mock import MagicMock, patch, call
from kombu import Connection, Exchange, Queue, Producer
from sfmutils.state_store import DictHarvestStateStore
from sfmutils.harvester import HarvestResult, EXCHANGE
import threading
import shutil
import tempfile
import time
import os
from datetime import datetime, date
from weibo_harvester import WeiboHarvester
from weiboarc import Weiboarc

vcr = base_vcr.VCR(
    cassette_library_dir='tests/fixtures',
    record_mode='once',
)


@unittest.skipIf(not tests.test_config_available, "Skipping test since test config not available.")
class TestWeiboHarvesterVCR(tests.TestCase):
    def setUp(self):
        self.harvester = WeiboHarvester()
        self.harvester.state_store = DictHarvestStateStore()
        self.harvester.harvest_result = HarvestResult()
        self.harvester.stop_event = threading.Event()
        self.harvester.harvest_result_lock = threading.Lock()
        self.harvester.message = {
            "id": "test:2",
            "type": "weibo_timeline",
            "path": "/collections/test_collection_set",
            "credentials": {
                "access_token": tests.WEIBO_ACCESS_TOKEN
            },
            "collection_set": {
                "id": "test_collection_set"
            },
            "options": {}
        }

    @vcr.use_cassette(filter_query_parameters=['access_token'])
    def test_search_vcr(self):
        self.harvester.harvest_seeds()
        # check the total number, for new users don't how to check
        self.assertEqual(self.harvester.harvest_result.stats_summary()["weibos"], 181)
        # check the harvester status
        self.assertTrue(self.harvester.harvest_result.success)

    @vcr.use_cassette(filter_query_parameters=['access_token'])
    def test_incremental_search_vcr(self):
        self.harvester.message["options"]["incremental"] = True
        collection_set_id = self.harvester.message["collection_set"]["id"]
        self.harvester.state_store.set_state("weibo_harvester", u"{}.since_id".format(collection_set_id), 3935747172100551)
        self.harvester.harvest_seeds()

        # Check harvest result
        self.assertTrue(self.harvester.harvest_result.success)
        # for check the number of get
        self.assertEqual(self.harvester.harvest_result.stats_summary()["weibos"], 5)
        # check the state
        self.assertEqual(3935776104305071, self.harvester.state_store.get_state("weibo_harvester",
                                                                                u"{}.since_id".format(
                                                                                    collection_set_id)))

    @vcr.use_cassette(filter_query_parameters=['access_token'])
    def test_default_harvest_options_vcr(self):
        self.harvester.harvest_seeds()
        # The default is none
        self.assertSetEqual(set(), self.harvester.harvest_result.urls_as_set())

    @vcr.use_cassette(filter_query_parameters=['access_token'])
    def test_harvest_options_web_vcr(self):
        self.harvester.message["options"]["web_resources"] = True
        self.harvester.message["options"]["media"] = False
        self.harvester.harvest_seeds()

        # Testing URL1&URL2
        self.assertEqual(104, len(self.harvester.harvest_result.urls_as_set()))

    @vcr.use_cassette(filter_query_parameters=['access_token'])
    def test_harvest_options_media_vcr(self):
        self.harvester.message["options"]["web_resources"] = False
        self.harvester.message["options"]["media"] = True
        self.harvester.harvest_seeds()

        # Testing URL3 photos URLs
        self.assertEqual(357, len(self.harvester.harvest_result.urls_as_set()))


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
            "path": "/collections/test_collection_set",
            "credentials": {
                "access_token": tests.WEIBO_ACCESS_TOKEN
            },
            "collection_set": {
                "id": "test_collection_set"
            },
            "options": {
                "web_resources": True,
                "media": True
            }
        }

    @patch("weibo_harvester.Weiboarc", autospec=True)
    def test_search_timeline(self, mock_weiboarc_class):
        mock_weiboarc = MagicMock(spec=Weiboarc)
        # Expecting 2 results. First returns 1tweets. Second returns none.
        mock_weiboarc.search_friendships.side_effect = [(weibo1, weibo2), ()]
        # Return mock_weiboarc when instantiating a weiboarc.
        mock_weiboarc_class.side_effect = [mock_weiboarc]

        self.harvester.harvest_seeds()
        self.assertDictEqual({"weibos": 2}, self.harvester.harvest_result.stats_summary())
        mock_weiboarc_class.assert_called_once_with(tests.WEIBO_ACCESS_TOKEN)

        self.assertEqual([call(since_id=None)], mock_weiboarc.search_friendships.mock_calls)
        # Nothing added to state
        self.assertEqual(0, len(self.harvester.state_store._state))

    @patch("weibo_harvester.Weiboarc", autospec=True)
    def test_incremental_search(self, mock_weiboarc_class):
        mock_weiboarc = MagicMock(spec=Weiboarc)
        # Expecting 2 searches. First returns 2 weibos,one is none. Second returns none.
        mock_weiboarc.search_friendships.side_effect = [(weibo2,), ()]
        # Return mock_weiboarc when instantiating a weiboarc.
        mock_weiboarc_class.side_effect = [mock_weiboarc]

        self.harvester.message["options"] = {
            # Incremental means that will only retrieve new results.
            "incremental": True
        }

        collection_set_id = self.harvester.message["collection_set"]["id"]
        self.harvester.state_store.set_state("weibo_harvester", u"{}.since_id".format(collection_set_id), 3927348724716740)
        self.harvester.harvest_seeds()

        self.assertDictEqual({"weibos": 1}, self.harvester.harvest_result.stats_summary())
        mock_weiboarc_class.assert_called_once_with(tests.WEIBO_ACCESS_TOKEN)

        # since_id must be in the mock calls
        self.assertEqual([call(since_id=3927348724716740)], mock_weiboarc.search_friendships.mock_calls)
        self.assertNotEqual([call(since_id=None)], mock_weiboarc.search_friendships.mock_calls)
        # State updated
        self.assertEqual(3928235789939265, self.harvester.state_store.get_state("weibo_harvester",
                                                                                u"{}.since_id".format(
                                                                                    collection_set_id)))

    def test_default_harvest_options(self):
        self.harvester.extract_media = False
        self.harvester.extract_web_resources = False

        self.harvester._process_weibos([weibo3, weibo4, weibo5])
        # The default will not sending web harvest
        self.assertSetEqual(set(), self.harvester.harvest_result.urls_as_set())

    def test_harvest_options_web(self):
        self.harvester.extract_media = False
        self.harvester.extract_web_resources = True

        self.harvester._process_weibos([weibo3, weibo4, weibo5])
        # Testing URL1&URL2
        self.assertSetEqual({
            'http://t.cn/RqmQ3ko',
            'http://m.weibo.cn/1618051664/3973767505640890'
        },
            self.harvester.harvest_result.urls_as_set())

    def test_harvest_options_media(self):
        self.harvester.extract_media = True
        self.harvester.extract_web_resources = False

        self.harvester._process_weibos([weibo3, weibo4, weibo5])
        # Testing URL3 photos URLs
        self.assertSetEqual({
            'http://ww2.sinaimg.cn/large/6b23a52bgw1f3pjhhyofnj208p06c3yq.jpg',
            'http://ww4.sinaimg.cn/large/60718250jw1f3qtzyhai3j20de0vin32.jpg'
        },
            self.harvester.harvest_result.urls_as_set())


@unittest.skipIf(not tests.test_config_available, "Skipping test since test config not available.")
@unittest.skipIf(not tests.integration_env_available, "Skipping test since integration env not available.")
class TestWeiboHarvesterIntegration(tests.TestCase):
    def _create_connection(self):
        return Connection(hostname="mq", userid=tests.mq_username, password=tests.mq_password)

    def setUp(self):
        self.exchange = Exchange(EXCHANGE, type="topic")
        self.result_queue = Queue(name="result_queue", routing_key="harvest.status.weibo.*", exchange=self.exchange,
                                  durable=True)
        self.web_harvest_queue = Queue(name="web_harvest_queue", routing_key="harvest.start.web",
                                       exchange=self.exchange)
        self.warc_created_queue = Queue(name="warc_created_queue", routing_key="warc_created", exchange=self.exchange)
        weibo_harvester_queue = Queue(name="weibo_harvester", exchange=self.exchange)
        with self._create_connection() as connection:
            self.result_queue(connection).declare()
            self.result_queue(connection).purge()
            self.web_harvest_queue(connection).declare()
            self.web_harvest_queue(connection).purge()
            self.warc_created_queue(connection).declare()
            self.warc_created_queue(connection).purge()
            # avoid raise NOT_FOUND error 404
            weibo_harvester_queue(connection).declare()
            weibo_harvester_queue(connection).purge()

        self.path = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def test_search(self):
        harvest_msg = {
            "id": "test:3",
            "type": "weibo_timeline",
            "path": self.path,
            "credentials": {
                "access_token": tests.WEIBO_ACCESS_TOKEN
            },
            "collection_set": {
                "id": "test_collection_set"
            },
            "options": {
                "web_resources": True,
                "media": True
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
            self.assertEqual("test:3", result_msg["id"])
            # Success
            self.assertEqual("completed success", result_msg["status"])
            # Some weibo posts
            self.assertTrue(result_msg["stats"][date.today().isoformat()]["weibos"])

            # Web harvest message.
            bound_web_harvest_queue = self.web_harvest_queue(connection)
            message_obj = bound_web_harvest_queue.get(no_ack=True)
            # the default value is not harvesting web resources.
            self.assertIsNotNone(message_obj, "No web harvest message.")
            web_harvest_msg = message_obj.payload
            # Some seeds
            self.assertTrue(len(web_harvest_msg["seeds"]))

            # Warc created message.
            bound_warc_created_queue = self.warc_created_queue(connection)
            message_obj = bound_warc_created_queue.get(no_ack=True)
            self.assertIsNotNone(message_obj, "No warc created message.")
            # check path exist
            warc_msg = message_obj.payload
            self.assertTrue(os.path.isfile(warc_msg["warc"]["path"]))
