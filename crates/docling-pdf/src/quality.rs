//! Per-page conversion-confidence scoring (#183) — the Rust port of docling's
//! confidence plumbing: `rate_text_quality` from the page-preprocessing stage
//! (parse quality of the extracted text layer) and the layout/OCR score
//! aggregation the layout-postprocessing and OCR stages assign.

use std::sync::OnceLock;

use docling_core::confidence::{nanmean, nanquantile, PageConfidence};
use regex::Regex;

use crate::layout::Region;
use crate::pdfium_backend::TextCell;

/// docling's `rate_text_quality`: score one text cell's extraction quality.
/// Hard garbage (replacement chars, `GLYPH<..>` references, `/G123`-style
/// glyph ids, mostly-slash-number content) is a 0.0; otherwise fragmented-word
/// runs (`W/or.ds/sp.lit` artifacts) are penalised 0.1 each once at least
/// three appear.
pub fn rate_text_quality(text: &str) -> f64 {
    static GLYPH_RE: OnceLock<Regex> = OnceLock::new();
    static SLASH_G_RE: OnceLock<Regex> = OnceLock::new();
    static FRAG_RE: OnceLock<Regex> = OnceLock::new();
    static SLASH_NUMBER_GARBAGE_RE: OnceLock<Regex> = OnceLock::new();
    let glyph = GLYPH_RE.get_or_init(|| Regex::new(r"GLYPH<[0-9A-Fa-f]+>").unwrap());
    let slash_g = SLASH_G_RE.get_or_init(|| Regex::new(r"(?:/G\d+){2,}").unwrap());
    let frag =
        FRAG_RE.get_or_init(|| Regex::new(r"\b[A-Za-z](?:/[a-z]{1,3}\.[a-z]{1,3}){2,}\b").unwrap());
    // Python's `.match` anchors at the start of the string only.
    let slash_number =
        SLASH_NUMBER_GARBAGE_RE.get_or_init(|| Regex::new(r"^(?:/\w+\s*){2,}").unwrap());

    if text.contains('\u{fffd}')
        || glyph.is_match(text)
        || slash_g.is_match(text)
        || slash_number.is_match(text)
    {
        return 0.0;
    }
    let frag_matches = frag.find_iter(text).count();
    let mut penalty = 0.0;
    if frag_matches >= 3 {
        penalty += 0.1 * frag_matches as f64;
    }
    (1.0 - penalty).max(0.0)
}

/// A whole text cell consisting of three or more unresolved synthetic PDF
/// glyph names. Only these all-opaque runs are eligible for a *local* OCR
/// fallback: ordinary text containing slash paths, IDs or code must not be
/// reinterpreted as missing glyphs.
pub fn is_opaque_glyph_run(text: &str) -> bool {
    let mut rest = text.trim();
    let mut count = 0;
    while !rest.is_empty() {
        rest = rest.trim_start();
        let Some(token) = rest.strip_prefix('/') else {
            return false;
        };
        let Some(tail) = ["glyph", "index", "cid", "CID", "g", "G"]
            .iter()
            .find_map(|prefix| token.strip_prefix(prefix))
        else {
            return false;
        };
        let digits = tail.bytes().take_while(u8::is_ascii_digit).count();
        if digits == 0 {
            return false;
        }
        rest = &tail[digits..];
        count += 1;
    }
    count >= 3
}

/// A glyph-recovery OCR attempt is only a substitute when every recognized
/// line has high confidence and readable text. Inconclusive OCR leaves the
/// original source tokens intact, without silently dropping the PDF layer.
pub fn usable_glyph_ocr(cells: &[(TextCell, f32)]) -> bool {
    !cells.is_empty()
        && cells.iter().map(|(c, _)| c.text.trim().chars().count()).sum::<usize>() >= 4
        && cells.iter().all(|(c, conf)| {
            conf.is_finite()
                && *conf >= 0.90
                && rate_text_quality(&c.text) > 0.0
                && !is_opaque_glyph_run(&c.text)
        })
}


/// Only an opaque native text cell fully owned by an ordinary text region
/// can enter speculative OCR. Exclude cells touching a structured or
/// non-text region so a nearby paragraph cannot override table, checkbox,
/// formula, or form semantics.
pub fn is_recoverable_native_glyph_cell(cell: &TextCell, regions: &[Region]) -> bool {
    if !is_opaque_glyph_run(&cell.text) {
        return false;
    }
    let w = cell.r - cell.l;
    let h = cell.b - cell.t;
    let area = w * h;
    if ![cell.l, cell.t, cell.r, cell.b, area]
        .iter()
        .all(|v| v.is_finite())
        || w <= 0.0
        || h <= 0.0
    {
        return false;
    }
    let overlap = |r: &Region| {
        let ix = (cell.r.min(r.r) - cell.l.max(r.l)).max(0.0);
        let iy = (cell.b.min(r.b) - cell.t.max(r.t)).max(0.0);
        ix * iy / area
    };
    if regions.iter().any(|r| {
        matches!(
            r.label,
            "table"
                | "document_index"
                | "form"
                | "key_value_region"
                | "picture"
                | "code"
                | "formula"
                | "checkbox_selected"
                | "checkbox_unselected"
        ) && overlap(r) > 0.05
    }) {
        return false;
    }
    regions.iter().any(|r| {
        matches!(
            r.label,
            "text" | "title" | "section_header" | "list_item" | "caption" | "footnote"
        ) && overlap(r) > 0.80
    })
}

/// Model confidence alone cannot establish that localized OCR belongs to the
/// selected source cell: the crop may also see adjacent text. Require every
/// output cell's finite, positive box to stay within a small padding of the
/// original native cell. Otherwise preserve the PDF tokens unchanged.
pub fn confined_glyph_ocr(cells: &[(TextCell, f32)], source: &TextCell) -> bool {
    const PAD: f32 = 3.0;
    if !usable_glyph_ocr(cells) {
        return false;
    }
    let source_w = source.r - source.l;
    let source_h = source.b - source.t;
    if ![source.l, source.t, source.r, source.b]
        .iter()
        .all(|v| v.is_finite())
        || source_w <= 0.0
        || source_h <= 0.0
    {
        return false;
    }
    cells.iter().all(|(c, _)| {
        [c.l, c.t, c.r, c.b].iter().all(|v| v.is_finite())
            && c.r > c.l
            && c.b > c.t
            && c.l >= source.l - PAD
            && c.t >= source.t - PAD
            && c.r <= source.r + PAD
            && c.b <= source.b + PAD
    })
}

/// The page `parse_score`: the 10th-percentile `rate_text_quality` over the
/// extracted text-layer cells (the quantile emphasises problem cells, matching
/// docling's page-preprocessing stage). Unset when the page has no text layer
/// (a scanned page — docling's `nanquantile([])` is `NaN` there too).
pub fn parse_score(cells: &[TextCell]) -> Option<f64> {
    let scores: Vec<Option<f64>> = cells
        .iter()
        .map(|c| Some(rate_text_quality(&c.text)))
        .collect();
    nanquantile(&scores, 0.10)
}

/// The page `layout_score`: mean confidence of the final (postprocessed)
/// layout regions — `Some(0.0)` for a region-less page, exactly like
/// docling's `float(np.mean(...)) if clusters else 0.0`.
///
/// Orphan-text regions carry the sentinel score `0.0` (detector scores always
/// clear their ≥ 0.3 label thresholds, so 0.0 can only mean "rescued cell").
/// docling scores an orphan cluster with its *cell's* confidence: 1.0 for a
/// text-layer cell, the recognition confidence for an OCR cell — substitute
/// accordingly (`ocr_mean` is the page's mean OCR confidence when the cells
/// came from OCR, `None` on a digital page).
pub fn layout_score(regions: &[Region], ocr_mean: Option<f64>) -> Option<f64> {
    if regions.is_empty() {
        return Some(0.0);
    }
    let scores: Vec<Option<f64>> = regions
        .iter()
        .map(|r| {
            if r.score == 0.0 {
                Some(ocr_mean.unwrap_or(1.0))
            } else {
                Some(r.score as f64)
            }
        })
        .collect();
    nanmean(&scores)
}

/// The page `ocr_score`: mean recognition confidence over the page's OCR'd
/// cells; unset when nothing on the page came from OCR (docling only assigns
/// it when OCR cells exist).
pub fn ocr_score(confs: &[f32]) -> Option<f64> {
    if confs.is_empty() {
        return None;
    }
    Some(confs.iter().map(|&c| c as f64).sum::<f64>() / confs.len() as f64)
}

/// Assemble the page's [`PageConfidence`] (`table_score` stays unset — docling
/// never assigns it either).
pub fn page_confidence(
    parse: Option<f64>,
    regions: &[Region],
    ocr_confs: &[f32],
) -> PageConfidence {
    let ocr = ocr_score(ocr_confs);
    PageConfidence {
        parse_score: parse,
        layout_score: layout_score(regions, ocr),
        table_score: None,
        ocr_score: ocr,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_quality_matches_docling_rules() {
        assert_eq!(rate_text_quality("A perfectly ordinary sentence."), 1.0);
        // Hard garbage → 0.0.
        assert_eq!(rate_text_quality("bad \u{fffd} char"), 0.0);
        assert_eq!(rate_text_quality("see GLYPH<0a3F> here"), 0.0);
        assert_eq!(rate_text_quality("/G12/G34 rest"), 0.0);
        assert_eq!(rate_text_quality("/x1 /y2 tail"), 0.0);
        // The slash-number pattern only fires anchored at the start.
        assert_eq!(rate_text_quality("word /x1 /y2"), 1.0);
        // Fragmented words (a letter then ≥2 `/xx.yy` runs): below three
        // matches no penalty applies; at three, 0.1 each.
        let frag = "W/or.ds/sp.lit";
        assert_eq!(rate_text_quality(frag), 1.0, "one match is tolerated");
        assert_eq!(rate_text_quality(&format!("{frag} {frag}")), 1.0);
        let three = format!("{frag} {frag} {frag}");
        assert!((rate_text_quality(&three) - 0.7).abs() < 1e-12, "{three}");
    }

    #[test]
    fn only_wholly_opaque_pdf_glyph_cells_are_ocr_candidates() {
        assert!(is_opaque_glyph_run("/g115/g3/g40/g81"));
        assert!(is_opaque_glyph_run("/cid14 /glyph20 /index33"));
        assert!(!is_opaque_glyph_run("/g115/g3"));
        assert!(!is_opaque_glyph_run("Chapter /g12/g13/g14"));
        assert!(!is_opaque_glyph_run("/g12 /real-path/file"));
        assert!(!is_opaque_glyph_run("g115/g3/g40"));
        assert!(!is_opaque_glyph_run("/g12/broken/g13"));
    }

    #[test]
    fn uncertain_glyph_ocr_never_replaces_original_native_text() {
        let cell = |text: &str| TextCell {
            text: text.into(), l: 10.0, t: 10.0, r: 90.0, b: 20.0,
        };
        assert!(usable_glyph_ocr(&[(cell("Readable label"), 0.97)]));
        assert!(!usable_glyph_ocr(&[(cell("Guess"), 0.70)]));
        assert!(!usable_glyph_ocr(&[(cell("/g11/g22/g33"), 0.99)]));
        assert!(!usable_glyph_ocr(&[(cell("A"), 0.99)]));
        assert!(!usable_glyph_ocr(&[]));
        assert!(!usable_glyph_ocr(&[(cell("Text"), f32::NAN)]));
    }


    #[test]
    fn glyph_ocr_requires_ordinary_region_without_structured_overlap() {
        let source = TextCell {
            text: "/g115/g3/g40/g81".into(),
            l: 10.0,
            t: 10.0,
            r: 90.0,
            b: 30.0,
        };
        let region = |label, l, r| Region {
            label,
            score: 0.99,
            l,
            t: 5.0,
            r,
            b: 35.0,
        };
        let normal = region("text", 5.0, 95.0);
        assert!(is_recoverable_native_glyph_cell(&source, &[normal.clone()]));
        assert!(!is_recoverable_native_glyph_cell(
            &source,
            &[normal.clone(), region("table", 10.0, 40.0)]
        ));
        assert!(!is_recoverable_native_glyph_cell(
            &source,
            &[normal.clone(), region("checkbox_selected", 10.0, 40.0)]
        ));
        assert!(!is_recoverable_native_glyph_cell(
            &source,
            &[region("formula", 5.0, 95.0)]
        ));
        let mut degenerate = source.clone();
        degenerate.r = degenerate.l;
        assert!(!is_recoverable_native_glyph_cell(&degenerate, &[normal.clone()]));
        let mut invalid = source;
        invalid.t = f32::NAN;
        assert!(!is_recoverable_native_glyph_cell(&invalid, &[normal]));
    }

    #[test]
    fn glyph_ocr_does_not_replace_source_with_neighboring_or_invalid_boxes() {
        let source = TextCell {
            text: "/g115/g3/g40/g81".into(),
            l: 10.0,
            t: 10.0,
            r: 90.0,
            b: 30.0,
        };
        let recognized = TextCell {
            text: "Readable label".into(),
            l: 11.0,
            t: 11.0,
            r: 89.0,
            b: 29.0,
        };
        assert!(confined_glyph_ocr(&[(recognized.clone(), 0.97)], &source));
        let mut neighbor = recognized.clone();
        neighbor.r = 150.0;
        assert!(!confined_glyph_ocr(&[(neighbor, 0.99)], &source));
        let mut invalid = recognized.clone();
        invalid.b = f32::INFINITY;
        assert!(!confined_glyph_ocr(&[(invalid, 0.99)], &source));
        let mut empty = recognized.clone();
        empty.b = empty.t;
        assert!(!confined_glyph_ocr(&[(empty, 0.99)], &source));
        assert!(!confined_glyph_ocr(&[(recognized, 0.5)], &source));
    }

    #[test]
    fn parse_score_is_tenth_percentile() {
        let cell = |text: &str| TextCell {
            text: text.into(),
            l: 0.0,
            t: 0.0,
            r: 1.0,
            b: 1.0,
        };
        assert_eq!(parse_score(&[]), None);
        // Nine clean cells and one garbage cell: the 10th percentile sits at
        // the interpolation between the sorted [0.0, 1.0 × 9] head.
        let mut cells = vec![cell("ok"); 9];
        cells.push(cell("GLYPH<12>"));
        let s = parse_score(&cells).unwrap();
        assert!((s - 0.9).abs() < 1e-9, "{s}");
    }

    #[test]
    fn layout_score_substitutes_orphan_sentinel() {
        let region = |score: f32| Region {
            label: "text",
            score,
            l: 0.0,
            t: 0.0,
            r: 1.0,
            b: 1.0,
        };
        assert_eq!(layout_score(&[], None), Some(0.0));
        // Digital page: the 0.0-score orphan counts as a 1.0-confidence cell.
        // (Tolerance covers the f32 detector score widened to f64.)
        let s = layout_score(&[region(0.8), region(0.0)], None).unwrap();
        assert!((s - 0.9).abs() < 1e-6, "{s}");
        // OCR'd page: orphans inherit the page's mean OCR confidence.
        let s = layout_score(&[region(0.8), region(0.0)], Some(0.6)).unwrap();
        assert!((s - 0.7).abs() < 1e-6, "{s}");
    }
}
