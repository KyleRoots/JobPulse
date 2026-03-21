from defusedxml.ElementTree import parse, fromstring, iterparse
from xml.etree.ElementTree import (
    Element, SubElement, tostring, ElementTree, Comment, ProcessingInstruction
)


class _SafeEtreeCompat:
    Element = Element
    SubElement = SubElement
    tostring = staticmethod(tostring)
    ElementTree = ElementTree
    Comment = Comment
    ProcessingInstruction = ProcessingInstruction
    parse = staticmethod(parse)
    fromstring = staticmethod(fromstring)
    iterparse = staticmethod(iterparse)


safe_etree = _SafeEtreeCompat()
