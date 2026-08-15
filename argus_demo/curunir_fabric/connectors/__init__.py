from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector
from .web_page import WebPageConnector
from .wayback import WaybackConnector
from .wikidata import WikidataConnector
from .gleif import GleifConnector
from .edgar import EdgarFullTextConnector
from .rss import RssFeedConnector

BUILTIN_CONNECTORS = {
    connector.connector_id: connector
    for connector in (WebPageConnector(), WaybackConnector(), WikidataConnector(),
                      GleifConnector(), EdgarFullTextConnector(), RssFeedConnector())
}

__all__ = ["ConnectorRequest", "ConnectorResponse", "NativeResult", "SourceConnector",
           "WebPageConnector", "WaybackConnector", "WikidataConnector", "GleifConnector",
           "EdgarFullTextConnector", "RssFeedConnector", "BUILTIN_CONNECTORS"]
