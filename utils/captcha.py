import random
import string
from PIL import Image, ImageDraw, ImageFont
import io
import base64
import os


class CaptchaGenerator:
    def __init__(self):
        self.width = 120
        self.height = 40
        self.length = 4
        self.font_size = 28

    def generate_captcha(self):
        """生成验证码"""
        # 生成随机字符（排除容易混淆的字符）
        chars = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
        code = ''.join(random.choices(chars, k=self.length))

        # 创建图片
        image = Image.new('RGB', (self.width, self.height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 添加干扰点
        for i in range(100):
            x = random.randint(0, self.width)
            y = random.randint(0, self.height)
            draw.point((x, y), fill=(random.randint(100, 200),
                                     random.randint(100, 200),
                                     random.randint(100, 200)))

        # 添加干扰线
        for i in range(3):
            x1 = random.randint(0, self.width)
            y1 = random.randint(0, self.height)
            x2 = random.randint(0, self.width)
            y2 = random.randint(0, self.height)
            draw.line([(x1, y1), (x2, y2)],
                      fill=(random.randint(150, 220),
                            random.randint(150, 220),
                            random.randint(150, 220)),
                      width=2)

        # 绘制文字
        for i, char in enumerate(code):
            x = 20 + i * 20
            y = random.randint(5, 15)
            # 为每个字符使用随机颜色
            color = (random.randint(0, 100),
                     random.randint(0, 100),
                     random.randint(0, 100))
            draw.text((x, y), char, fill=color)

        # 转换为base64
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()

        return code, img_str


# 创建全局实例
captcha_gen = CaptchaGenerator()