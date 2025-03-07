import datetime as dt
from base64 import b64encode
from collections.abc import Sequence
from dataclasses import field

from loguru import logger
from pydantic import BaseModel

from notte.actions.base import Action
from notte.browser.dom_tree import A11yTree, DomNode, InteractionDomNode
from notte.errors.base import AccessibilityTreeMissingError
from notte.pipe.preprocessing.a11y.traversal import set_of_interactive_nodes
from notte.utils.url import clean_url


class TabsData(BaseModel):
    tab_id: int
    title: str
    url: str


class ViewportData(BaseModel):
    scroll_x: int
    scroll_y: int
    viewport_width: int
    viewport_height: int
    total_width: int
    total_height: int

    @property
    def pixels_above(self) -> int:
        return self.scroll_y

    @property
    def pixels_below(self) -> int:
        return self.total_height - self.scroll_y - self.viewport_height


class SnapshotMetadata(BaseModel):
    title: str
    url: str
    viewport: ViewportData
    tabs: list[TabsData]
    timestamp: dt.datetime = field(default_factory=dt.datetime.now)


class BrowserSnapshot(BaseModel):
    metadata: SnapshotMetadata
    html_content: str
    a11y_tree: A11yTree | None
    dom_node: DomNode
    screenshot: bytes | None

    model_config = {  # type: ignore[reportUnknownMemberType]
        "json_encoders": {
            bytes: lambda v: b64encode(v).decode("utf-8") if v else None,
        }
    }

    @property
    def clean_url(self) -> str:
        return clean_url(self.metadata.url)

    def compare_with(self, other: "BrowserSnapshot") -> bool:
        if self.a11y_tree is None or other.a11y_tree is None:
            raise AccessibilityTreeMissingError()

        inodes = set_of_interactive_nodes(self.a11y_tree.simple)
        new_inodes = set_of_interactive_nodes(other.a11y_tree.simple)
        identical = inodes == new_inodes
        if not identical:
            logger.warning(f"Interactive nodes changed: {new_inodes.difference(inodes)}")
        return identical

    def interaction_nodes(self) -> Sequence[InteractionDomNode]:
        return self.dom_node.interaction_nodes()

    def with_dom_node(self, dom_node: DomNode) -> "BrowserSnapshot":
        return BrowserSnapshot(
            metadata=self.metadata,
            html_content=self.html_content,
            a11y_tree=self.a11y_tree,
            dom_node=dom_node,
            screenshot=self.screenshot,
        )

    def subgraph_without(self, actions: Sequence[Action], roles: set[str] | None = None) -> "BrowserSnapshot | None":
        if len(actions) == 0 and roles is not None:
            subgraph = self.dom_node.subtree_without(roles)
            return self.with_dom_node(subgraph)
        id_existing_actions = set([action.id for action in actions])
        failed_actions = {node.id for node in self.interaction_nodes() if node.id not in id_existing_actions}

        def only_failed_actions(node: DomNode) -> bool:
            return len(set(node.subtree_ids).intersection(failed_actions)) > 0

        filtered_graph = self.dom_node.subtree_filter(only_failed_actions)
        if filtered_graph is None:
            return None

        return self.with_dom_node(filtered_graph)
