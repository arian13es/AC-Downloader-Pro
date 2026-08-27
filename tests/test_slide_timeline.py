import unittest
from pathlib import Path
from tempfile import mkdtemp

from core.slide_timeline import (
    parse_document_descriptors,
    parse_slide_schedule,
)
from utils.logger import logger

logger.disabled = True

MAINSTREAM = """<root>
<Message time="0" type="data">
 <Method><![CDATA[playEvent]]></Method>
 <Object><id>23</id><name>ContentManagerId_Mainstream</name><time>162</time></Object>
 <String><![CDATA[setContentSo]]></String>
 <Array><Object>
   <code><![CDATA[change]]></code>
   <name><![CDATA[5]]></name>
   <newValue>
     <breakoutRoomID><![CDATA[0]]></breakoutRoomID>
     <ctID><![CDATA[5]]></ctID>
     <documentDescriptor>
       <contentOutputPath><![CDATA[/_a7/pAAA/default/]]></contentOutputPath>
       <originatingSco><![CDATA[111]]></originatingSco>
       <scoID><![CDATA[222]]></scoID>
     </documentDescriptor>
   </newValue>
 </Object></Array>
</Message>
<Message time="292000" type="data">
 <Method><![CDATA[playEvent]]></Method>
 <Object><id>106</id><name>ContentManagerId_Mainstream</name><time>292829</time></Object>
 <String><![CDATA[setContentSo]]></String>
 <Array><Object>
   <code><![CDATA[change]]></code>
   <name><![CDATA[7]]></name>
   <newValue>
     <ctID><![CDATA[7]]></ctID>
     <documentDescriptor>
       <contentOutputPath><![CDATA[/_a7/pBBB/default/]]></contentOutputPath>
       <originatingSco><![CDATA[333]]></originatingSco>
       <scoID><![CDATA[444]]></scoID>
     </documentDescriptor>
   </newValue>
 </Object></Array>
</Message>
</root>"""

FTCONTENT = """<root>
<Message time="0" type="data"><Method><![CDATA[playEvent]]></Method>
 <String><![CDATA[setContentSo]]></String>
 <Array><Object><code><![CDATA[change]]></code><name><![CDATA[ctID]]></name>
 <newValue><![CDATA[5]]></newValue></Object></Array></Message>
<Message time="100" type="data"><Method><![CDATA[playEvent]]></Method>
 <String><![CDATA[setPdfContentSo]]></String>
 <Array><Object><code><![CDATA[change]]></code><name><![CDATA[memento]]></name>
 <newValue><![CDATA[|rotn-0|tPgNum-17|bPgNum-17|AR-1.33]]></newValue></Object></Array></Message>
<Message time="50000" type="data"><Method><![CDATA[playEvent]]></Method>
 <String><![CDATA[setPdfContentSo]]></String>
 <Array><Object><code><![CDATA[change]]></code><name><![CDATA[memento]]></name>
 <newValue><![CDATA[|rotn-0|tPgNum-18|bPgNum-18|AR-1.33]]></newValue></Object></Array></Message>
<Message time="90000" type="data"><Method><![CDATA[playEvent]]></Method>
 <String><![CDATA[setContentSo]]></String>
 <Array><Object><code><![CDATA[change]]></code><name><![CDATA[ctID]]></name>
 <newValue><![CDATA[7]]></newValue></Object></Array></Message>
<Message time="90500" type="data"><Method><![CDATA[playEvent]]></Method>
 <String><![CDATA[setPdfContentSo]]></String>
 <Array><Object><code><![CDATA[change]]></code><name><![CDATA[memento]]></name>
 <newValue><![CDATA[|rotn-0|tPgNum-0|bPgNum-0|AR-1.33]]></newValue></Object></Array></Message>
</root>"""


class TestSlideTimeline(unittest.TestCase):
    def setUp(self):
        self.dir = Path(mkdtemp())
        (self.dir / "mainstream.xml").write_text(MAINSTREAM, encoding="utf-8")
        (self.dir / "ftcontent1.xml").write_text(FTCONTENT, encoding="utf-8")

    def test_descriptor_order_stable(self):
        descs = parse_document_descriptors(MAINSTREAM)
        self.assertEqual(len(descs), 2)
        self.assertEqual(descs[0]["originatingSco"], "111")
        self.assertEqual(descs[1]["originatingSco"], "333")

    def test_schedule_reconstruction(self):
        events = parse_slide_schedule(self.dir)
        # beats: blank@0 (pre-content), (doc1,p17)@100, (doc1,p18)@50k,
        #        blank@90k (document switch gap), (doc2,p0)@90.5k
        self.assertEqual(len(events), 5)
        self.assertEqual((events[1].time_ms, events[1].page_num, events[1].doc_index), (100, 17, 1))
        self.assertEqual((events[2].time_ms, events[2].page_num, events[2].doc_index), (50000, 18, 1))
        self.assertEqual(events[3].page_num, -1)  # switch gap -> blank
        self.assertEqual((events[4].time_ms, events[4].page_num, events[4].doc_index), (90500, 0, 2))

    def test_empty_dir_returns_no_events(self):
        self.assertEqual(parse_slide_schedule(Path(mkdtemp())), [])


if __name__ == "__main__":
    unittest.main()
