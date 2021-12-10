import cgi
import json
import logging
from argparse import ArgumentParser
from enum import Enum

import requests
import yaml
import htmlmin
from bs4 import BeautifulSoup, element
from jinja2 import Template


class Type(Enum):
    lead = 0
    sub_lead = 1
    body = 2
    image = 3

    empty = 0xFF


def tag_type(tag: element.Tag) -> Type:
    if tag.img:
        return Type.image

    if not tag.text:
        return Type.empty

    if tag.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        return Type.lead

    if tag.strong and tag.strong.next_sibling is None and tag.strong.previous_sibling is None:
        return Type.sub_lead

    return Type.body


class Paragraph:
    def __init__(self):
        self.leads: list[str] = []
        self.body: list[str] = []
        self.subs: Paragraph | None = None
        self.img_url: str | None = None

    @property
    def lead(self):
        return ','.join(self.leads)

    @property
    def img_src(self):
        return self.subs.img_url if self.subs else self.img_url


class WeArticle:
    API_BASE_URL = 'https://api.weixin.qq.com/cgi-bin'

    def __init__(self, config):
        self.access_token: str = '51_6VySlE4ID1alSWsSXFCZYi0_QRN-3LP41zjOxNiHtSgqwiFx3HKAVRoec94i-lW2YEpzSO4F5Bva-QXrq2ppORoiwvSUmLuJqV48F7IXTnJjODC1av27rgSaKDSrlf3NOe4BmvKJA6q7TW1JMCSeAIAHXN'

        self.title: str = config['title']
        self.author: str = config['author']
        self.digest = config['digest']
        self.paras: list[Paragraph] = []

    @staticmethod
    def cache_img(img_url: str) -> str:
        resp = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://shimo.im/'}, stream=True)

        if resp.ok:
            filename = cgi.parse_header(resp.headers['content-disposition'])[1]['filename']
            filename = f'tmp/{filename}'
            with open(filename, 'wb') as fobj:
                fobj.write(resp.content)

            return filename
        else:
            logging.error(f'Failed to download image {img_url}.\nError: {resp.json()}')

    def upload_img(self, img_url: str):
        filename = WeArticle.cache_img(img_url)

        with open(filename, 'rb') as file_obj:
            resp = requests.post(f'{WeArticle.API_BASE_URL}/media/uploadimg?access_token={self.access_token}', files={'media': file_obj})
            if resp.ok and not resp.json().get('errcode'):
                return resp.json()['url']
            else:
                logging.error(f'Failed to upload image {filename}.\nError: {resp.json()}')

    def create_draft(self, content: str):
        payload = {'articles': [{'title': self.title, 'author': self.author, 'content': content, 'thumb_media_id': 'gQOp_H1dB3TUt_Jiz4f-mmjQG71d9QtLQO5oq6ZVv7w'}]}
        resp = requests.post(f'{WeArticle.API_BASE_URL}/draft/add?access_token={self.access_token}', data=payload)

        if resp.ok and not resp.json().get('errcode'):
            pass
        else:
            logging.error(f'Failed to post draft.\nError: {resp.json()}')

    def update_draft(self, media_id: str, content: str):
        payload = {
            'media_id': media_id,
            'index': 0,
            'articles': {
                'title': 'test',
                'author': self.author,
                'digest': self.digest,
                'content': htmlmin.minify(content),
                'content_source_url': '',
                'thumb_media_id': 'gQOp_H1dB3TUt_Jiz4f-mmjQG71d9QtLQO5oq6ZVv7w',
                'show_cover_pic': 0,
                'thumb_url': '',
                'need_open_comment': False,
                'only_fans_can_comment': True
            }
        }

        resp = requests.post(f'{WeArticle.API_BASE_URL}/draft/update?access_token={self.access_token}', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        if resp.ok and not resp.json().get('errcode'):
            pass
        else:
            logging.error(f'Failed to post draft.\nError: {resp.json()}')

    def parse_doc(self, link: str, dump: bool = True):
        resp = requests.get(link, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://shimo.im/'})
        soup = BeautifulSoup(resp.content, 'lxml')
        editor = soup.find('div', class_='ql-editor')

        para: Paragraph | None = None

        for child in editor.children:
            tag = tag_type(child)
            text = child.text
            # print(text)

            match tag:
                case Type.lead:
                    self.paras.append(Paragraph())
                    para = self.paras[-1]
                    para.leads.append(text)
                case Type.sub_lead:
                    para.subs = Paragraph()
                    para = para.subs
                    para.leads.append(text)
                case Type.image if para:
                    para.img_url = self.upload_img(child.img['src'])
                case Type.body if para:  # if not para.subs and para.lead:
                    para.body.append(text)
                # case Type.body if para.subs and para.subs.lead:
                #     para.subs.body.append(text)

        if dump:
            with open('tmp/paras.yaml', 'w', encoding='utf-8') as stream:
                yaml.dump(self.paras, stream, allow_unicode=True)

    def render(self):
        with open('template.html', encoding='utf-8') as fin:
            template = Template(fin.read(), trim_blocks=True, lstrip_blocks=True)

            return template.render(title=self.title, author=self.author, paras=self.paras)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('-l', '--link')
    args = parser.parse_args()

    with open('config.yaml', 'r', encoding='utf-8') as fin:
        article = WeArticle(config=yaml.safe_load(fin))
        article.parse_doc(args.link)
        res = article.render()
        article.update_draft('gQOp_H1dB3TUt_Jiz4f-mgKQ5khPhm8sAlqAGnEH8FY', res)

        with open('tmp/result.html', 'w', encoding='utf-8') as fout:
            fout.write(res)
