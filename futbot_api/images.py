"""Image helpers for FutBot card inspection."""
import io

from PIL import Image, ImageDraw, ImageFont, ImageOps


def generate_minted_card(card_path, avatar_bytes, owner_name, edition_text):
    """Render the original FutBot owner badge and edition plate onto a card."""
    try:
        card_img = Image.open(card_path).convert("RGBA")
        card_w, card_h = card_img.size
        draw = ImageDraw.Draw(card_img)

        scale_factor = card_w / 1000
        font_size_main = max(20, int(45 * scale_factor))
        pfp_size = max(40, int(90 * scale_factor))
        padding = int(20 * scale_factor)
        edge_margin = int(30 * scale_factor)
        border_width = max(1, int(3 * scale_factor))
        corner_radius = max(1, int(25 * scale_factor))

        try:
            font_owner = ImageFont.truetype("arialbd.ttf", font_size_main)
            font_edition = ImageFont.truetype("arialbd.ttf", font_size_main)
        except OSError:
            font_owner = ImageFont.load_default()
            font_edition = ImageFont.load_default()

        owner_bbox = draw.textbbox((0, 0), owner_name, font=font_owner)
        owner_text_w = owner_bbox[2] - owner_bbox[0]
        owner_text_h = owner_bbox[3] - owner_bbox[1]
        owner_pill_w = pfp_size + owner_text_w + (padding * 3)
        owner_pill_h = pfp_size + padding

        owner_pill = Image.new("RGBA", (owner_pill_w, owner_pill_h), (0, 0, 0, 0))
        pill_draw = ImageDraw.Draw(owner_pill)
        pill_draw.rounded_rectangle(
            [(0, 0), (owner_pill_w, owner_pill_h)],
            radius=corner_radius,
            fill=(20, 20, 20, 245),
            outline=(255, 255, 255, 150),
            width=max(1, int(border_width / 2)),
        )

        if avatar_bytes:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            avatar_img = avatar_img.resize((pfp_size, pfp_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (pfp_size, pfp_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, pfp_size, pfp_size), fill=255)
            circular_avatar = ImageOps.fit(avatar_img, mask.size, centering=(0.5, 0.5))
            circular_avatar.putalpha(mask)
            pfp_y = (owner_pill_h - pfp_size) // 2
            owner_pill.paste(circular_avatar, (padding, pfp_y), circular_avatar)

        text_x = padding * 2 + pfp_size
        text_y = (owner_pill_h - owner_text_h) // 2 - int(5 * scale_factor)
        pill_draw.text((text_x, text_y), owner_name, font=font_owner, fill="white")
        card_img.paste(
            owner_pill,
            (edge_margin, card_h - owner_pill_h - edge_margin),
            owner_pill,
        )

        edition_bbox = draw.textbbox((0, 0), edition_text, font=font_edition)
        edition_w = edition_bbox[2] - edition_bbox[0]
        edition_h = edition_bbox[3] - edition_bbox[1]
        plate_w = edition_w + (padding * 4)
        plate_h = owner_pill_h
        plate = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
        plate_draw = ImageDraw.Draw(plate)
        plate_draw.rounded_rectangle(
            [(0, 0), (plate_w, plate_h)],
            radius=corner_radius,
            fill=(30, 30, 30, 245),
            outline=(218, 165, 32, 255),
            width=border_width,
        )
        text_x = (plate_w - edition_w) // 2
        text_y = (plate_h - edition_h) // 2 - int(5 * scale_factor)
        plate_draw.text((text_x, text_y), edition_text, font=font_edition, fill=(255, 223, 0, 255))
        card_img.paste(
            plate,
            (card_w - plate_w - edge_margin, card_h - plate_h - edge_margin),
            plate,
        )

        output = io.BytesIO()
        card_img.save(output, format="PNG")
        return output.getvalue()
    except Exception as exc:
        raise ValueError("Could not generate the minted card image.") from exc
