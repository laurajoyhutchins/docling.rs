//! Reproduce the same page's pixels through two PDF renderers.
//!
//! Source-fidelity diagnostic (#22), not a replacement for the Python
//! reference. Reuses the existing PDF renderer entrypoint; no new runtime deps.
//!
//! cargo run --release -p docling-pdf --example renderer_diff -- render input.pdf out.png 1
//! cargo run --release -p docling-pdf --example renderer_diff -- compare rust.png parse.png
//!
//! Render in separate processes with DOCLING_RS_RENDERER=rust and
//! DOCLING_RS_RENDERER=docling-parse. Both render at one pixel per PDF point.

use std::{env, error::Error, fs};

use docling_pdf::pdfium_backend::render_pages;
use image::RgbImage;

#[derive(Debug, PartialEq)]
struct PixelDiff {
    width: u32,
    height: u32,
    differing_pixels: u64,
    total_pixels: u64,
    max_channel_difference: u8,
    mean_absolute_channel_difference: f64,
}

fn measure(a: &RgbImage, b: &RgbImage) -> Result<PixelDiff, String> {
    if a.dimensions() != b.dimensions() {
        return Err(format!(
            "dimension mismatch: {:?} versus {:?}",
            a.dimensions(),
            b.dimensions()
        ));
    }
    let mut differing_pixels = 0u64;
    let mut total_difference = 0u64;
    let mut max_difference = 0u8;
    for (left, right) in a.pixels().zip(b.pixels()) {
        let mut changed = false;
        for channel in 0..3 {
            let difference = left[channel].abs_diff(right[channel]);
            changed |= difference > 0;
            total_difference += u64::from(difference);
            max_difference = max_difference.max(difference);
        }
        differing_pixels += u64::from(changed);
    }
    let total_pixels = u64::from(a.width()) * u64::from(a.height());
    let mean_absolute_channel_difference = if total_pixels == 0 {
        0.0
    } else {
        total_difference as f64 / (total_pixels * 3) as f64
    };
    Ok(PixelDiff {
        width: a.width(),
        height: a.height(),
        differing_pixels,
        total_pixels,
        max_channel_difference,
        mean_absolute_channel_difference,
    })
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.as_slice() {
        [command, source, destination, page] if command == "render" => {
            let page: usize = page.parse()?;
            if page == 0 {
                return Err("page number must be one-based".into());
            }
            let bytes = fs::read(source)?;
            let pages = render_pages(&bytes, None, Some((page, page)), 1.0)?;
            let only = pages.first().ok_or("renderer returned no page")?;
            if pages.len() != 1 || only.page_no != page || only.png.is_empty() {
                return Err("renderer returned unexpected page or empty PNG".into());
            }
            fs::write(destination, &only.png)?;
            println!(
                "PIXEL_RENDERED page={} width={} height={} output={}",
                page, only.width, only.height, destination
            );
        }
        [command, left, right] if command == "compare" => {
            let a = image::load_from_memory(&fs::read(left)?)?.to_rgb8();
            let b = image::load_from_memory(&fs::read(right)?)?.to_rgb8();
            let diff = measure(&a, &b)?;
            println!(
                "PIXEL_RENDER_DIFF width={} height={} different={}/{} max_channel={} mean_abs_channel={:.9}",
                diff.width,
                diff.height,
                diff.differing_pixels,
                diff.total_pixels,
                diff.max_channel_difference,
                diff.mean_absolute_channel_difference,
            );
        }
        _ => {
            return Err(
                "usage: renderer_diff render <pdf> <png> <page> | compare <rust.png> <parse.png>"
                    .into(),
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{measure, RgbImage};

    #[test]
    fn pixel_difference_preserves_channel_scale_and_pixel_coverage() {
        let mut a = RgbImage::new(2, 1);
        let mut b = RgbImage::new(2, 1);
        a.put_pixel(0, 0, image::Rgb([10, 10, 10]));
        b.put_pixel(0, 0, image::Rgb([13, 11, 10]));
        let d = measure(&a, &b).expect("same size");
        assert_eq!((d.differing_pixels, d.total_pixels), (1, 2));
        assert_eq!(d.max_channel_difference, 3);
        assert!((d.mean_absolute_channel_difference - 4.0 / 6.0).abs() < 1e-8);
    }

    #[test]
    fn dimension_mismatch_is_not_reported_as_pixel_parity() {
        assert!(measure(&RgbImage::new(2, 1), &RgbImage::new(1, 2)).is_err());
    }
}
