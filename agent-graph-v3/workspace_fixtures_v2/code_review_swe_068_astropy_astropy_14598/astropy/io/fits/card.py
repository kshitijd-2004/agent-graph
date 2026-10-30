import re
import warnings
import numpy as np
from astropy.utils.exceptions import AstropyUserWarning
from . import conf
from .util import _is_int, _str_to_num, _words_group, translate
from .verify import VerifyError, VerifyWarning, _ErrList, _Verify
class Card(_Verify):
    # This will match any printable ASCII character excluding '='
    _keywd_hierarch_RE = re.compile(r"^(?:HIERARCH +)?(?:^[ -<>-~]+ ?)+$", re.I)

    # A number sub-string, either an integer or a float in fixed or
    # scientific notation.  One for FSC and one for non-FSC (NFSC) format:
    # NFSC allows lower case of DE for exponent, allows space between sign,
    # digits, exponent sign, and exponents
    _digits_FSC = r"(\.\d+|\d+(\.\d*)?)([DE][+-]?\d+)?"
    _digits_NFSC = r"(\.\d+|\d+(\.\d*)?) *([deDE] *[+-]? *\d+)?"
    _numr_FSC = r"[+-]?" + _digits_FSC
    _numr_NFSC = r"[+-]? *" + _digits_NFSC

    # This regex helps delete leading zeros from numbers, otherwise
    # Python might evaluate them as octal values (this is not-greedy, however,
    # so it may not strip leading zeros from a float, which is fine)
    _number_FSC_RE = re.compile(rf"(?P<sign>[+-])?0*?(?P<digt>{_digits_FSC})")
    _number_NFSC_RE = re.compile(rf"(?P<sign>[+-])? *0*?(?P<digt>{_digits_NFSC})")

    # Used in cards using the CONTINUE convention which expect a string
    # followed by an optional comment
    _strg = r"\'(?P<strg>([ -~]+?|\'\'|) *?)\'(?=$|/| )"
    _comm_field = r"(?P<comm_field>(?P<sepr>/ *)(?P<comm>(.|\n)*))"
    _strg_comment_RE = re.compile(f"({_strg})? *{_comm_field}?")

    # FSC commentary card string which must contain printable ASCII characters.
    # Note: \Z matches the end of the string without allowing newlines
    _ascii_text_re = re.compile(r"[ -~]*\Z")

    # Checks for a valid value/comment string.  It returns a match object
    # for a valid value/comment string.
    # The valu group will return a match if a FITS string, boolean,
    # number, or complex value is found, otherwise it will return
    # None, meaning the keyword is undefined.  The comment field will
    # return a match if the comment separator is found, though the
    # comment maybe an empty string.
    # fmt: off

    def _split(self):
        """
        Split the card image between the keyword and the rest of the card.
        """
        if self._image is not None:
            # If we already have a card image, don't try to rebuild a new card
            # image, which self.image would do
            image = self._image
        else:
            image = self.image

        # Split cards with CONTINUE cards or commentary keywords with long
        # values
        if len(self._image) > self.length:
            values = []
            comments = []
            keyword = None
            for card in self._itersubcards():
                kw, vc = card._split()
                if keyword is None:
                    keyword = kw

                if keyword in self._commentary_keywords:
                    values.append(vc)
                    continue

                # Should match a string followed by a comment; if not it
                # might be an invalid Card, so we just take it verbatim
                m = self._strg_comment_RE.match(vc)
                if not m:
                    return kw, vc

                value = m.group("strg") or ""
                value = value.rstrip().replace("''", "'")
                if value and value[-1] == "&":
                    value = value[:-1]
                values.append(value)
                comment = m.group("comm")
                if comment:
                    comments.append(comment.rstrip())

            if keyword in self._commentary_keywords:
                valuecomment = "".join(values)
            else:
                # CONTINUE card
                valuecomment = f"'{''.join(values)}' / {' '.join(comments)}"
            return keyword, valuecomment

        if self.keyword in self._special_keywords:
            keyword, valuecomment = image.split(" ", 1)
        else:
            try:
                delim_index = image.index(self._value_indicator)
            except ValueError:
                delim_index = None

            # The equal sign may not be any higher than column 10; anything
            # past that must be considered part of the card value
            if delim_index is None:
                keyword = image[:KEYWORD_LENGTH]
                valuecomment = image[KEYWORD_LENGTH:]
            elif delim_index > 10 and image[:9] != "HIERARCH ":
                keyword = image[:8]
                valuecomment = image[8:]
            else:
                keyword, valuecomment = image.split(self._value_indicator, 1)
        return keyword.strip(), valuecomment.strip()

    def _fix_keyword(self):
        if self.field_specifier:
            keyword, field_specifier = self._keyword.split(".", 1)
            self._keyword = ".".join([keyword.upper(), field_specifier])
        else:
            self._keyword = self._keyword.upper()
        self._modified = True

