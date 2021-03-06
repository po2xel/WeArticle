import cgi
import json
import logging
import re
from argparse import ArgumentParser
from enum import Enum

import htmlmin
import requests
import yaml
from bs4 import BeautifulSoup, element
from jinja2 import Template


class Type(Enum):
    lead = 0
    sub_lead = 1
    body = 2
    image = 3

    empty = 0xFF


class Material(Enum):
    image = 'image'
    voice = 'voice'
    video = 'video'
    thumb = 'thumb'

    def __str__(self):
        return self.value


def tag_type(tag: element.Tag) -> Type:
    tag_type.HEADINGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')

    if tag.img:
        return Type.image

    if not tag.text:
        return Type.empty

    if tag.name in tag_type.HEADINGS:
        if tag.name == 'h1' or not tag.find_previous_sibling(tag_type.HEADINGS[0: tag_type.HEADINGS.index(tag.name)]):
            return Type.lead
        else:
            return Type.sub_lead

    if tag.strong and not (tag.strong.next_sibling or tag.strong.previous_sibling):
        return Type.sub_lead

    return Type.body


class Config(yaml.YAMLObject):
    yaml_tag = '!Configurations'
    yaml_loader = yaml.SafeLoader

    class Draft(yaml.YAMLObject):
        yaml_tag = '!Draft'
        yaml_loader = yaml.SafeLoader

        def __init__(self, title, author, digest, source_url, thumb, show_cover_pic, need_open_comment, only_fans_can_comment):
            self.title = title
            self.author = author
            self.digest = digest
            self.source_url = source_url
            self.thumb = thumb
            self.show_cover_pic = show_cover_pic
            self.need_open_comment = need_open_comment
            self.only_fans_can_comment = only_fans_can_comment

    def __init__(self, appid, secret, draft):
        self.appid = appid
        self.secret = secret
        self.draft = draft


class Paragraph(yaml.YAMLObject):
    yaml_tag = '!Paragraph'
    yaml_loader = yaml.SafeLoader

    def __init__(self):
        self.lead: str = ''
        self.theme: str = ''
        self.body: list[str] = []
        self.subs: list[Paragraph] = []
        self.img_url: str | None = None

    @property
    def leads(self):
        return re.split('[,???]', self.lead)

    @property
    def img_src(self):
        return self.img_url


class WeArticle:
    API_BASE_URL = 'https://api.weixin.qq.com/cgi-bin'

    def __init__(self, config: Config):
        self.appid = config.appid
        self.secret = config.secret

        self.access_token, _ = self._get_access_token()

        self.title: str = config.draft.title
        self.author: str = config.draft.author
        self.digest = config.draft.digest
        self.source_url = config.draft.source_url
        self.thumb_media_id = self.upload_thumb(config.draft.thumb)
        self.show_cover_pic = config.draft.show_cover_pic
        self.need_open_comment = config.draft.need_open_comment
        self.only_fans_can_comment = config.draft.only_fans_can_comment

        self.paras: list[Paragraph] = []

    def _get_access_token(self):
        resp = requests.get(f'{WeArticle.API_BASE_URL}/token?grant_type=client_credential&appid={self.appid}&secret={self.secret}')
        if resp.ok and not resp.json().get('errcode'):
            return resp.json()['access_token'], resp.json()['expires_in']
        else:
            logging.error(f'Failed to acquired access token.\nError: {resp.json()}')
            raise PermissionError(resp.json())

    def _upload_material(self, filename: str, material: Material):
        with open(filename, 'rb') as img:
            resp = requests.post(f'{WeArticle.API_BASE_URL}/material/add_material?access_token={self.access_token}&type={material}', files={'media': img})
            if resp.ok and not resp.json().get('errcode'):
                return resp.json()['media_id']
            else:
                logging.error(f'Failed to upload thumbnail {filename}.\nError: {resp.json()}')

    @staticmethod
    def cache_img(img_url: str) -> str:
        resp = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://shimo.im/'}, stream=True)

        if resp.ok:
            filename = cgi.parse_header(resp.headers['content-disposition'])[1]['filename']
            filename = f'tmp/{filename}'
            with open(filename, 'wb') as img:
                img.write(resp.content)

            return filename
        else:
            logging.error(f'Failed to download image {img_url}.\nError: {resp.json()}')

    def upload_img(self, img_url: str):
        filename = WeArticle.cache_img(img_url)

        with open(filename, 'rb') as img:
            resp = requests.post(f'{WeArticle.API_BASE_URL}/media/uploadimg?access_token={self.access_token}', files={'media': img})
            if resp.ok and not resp.json().get('errcode'):
                return resp.json()['url']
            else:
                logging.error(f'Failed to upload image {filename}.\nError: {resp.json()}')

    def upload_thumb(self, filename: str):
        return self._upload_material(filename, Material.image)

    def create_draft(self, content: str):
        payload = {
            'articles': [{
                'title': self.title,
                'author': self.author,
                'digest': self.digest,
                'content': htmlmin.minify(content),
                'content_source_url': self.source_url,
                'thumb_media_id': self.thumb_media_id,
                'show_cover_pic': self.show_cover_pic,
                'need_open_comment': self.need_open_comment,
                'only_fans_can_comment': self.only_fans_can_comment
            }]
        }

        resp = requests.post(f'{WeArticle.API_BASE_URL}/draft/add?access_token={self.access_token}', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        if resp.ok and not resp.json().get('errcode'):
            return resp.json()['media_id']
        else:
            logging.error(f'Failed to post draft.\nError: {resp.json()}')

    def update_draft(self, media_id: str, content: str):
        payload = {
            'media_id': media_id,
            'index': 0,
            'articles': {
                'title': self.title,
                'author': self.author,
                'digest': self.digest,
                'content': htmlmin.minify(content),
                'content_source_url': self.source_url,
                'thumb_media_id': self.thumb_media_id,
                'show_cover_pic': self.show_cover_pic,
                'need_open_comment': self.need_open_comment,
                'only_fans_can_comment': self.only_fans_can_comment
            }
        }

        resp = requests.post(f'{WeArticle.API_BASE_URL}/draft/update?access_token={self.access_token}', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        if resp.ok and not resp.json().get('errcode'):
            pass
        else:
            logging.error(f'Failed to post draft.\nError: {resp.json()}')

    def load_structs(self, struct_file: str):
        with open(struct_file, encoding='utf-8') as struct:
            self.paras = yaml.safe_load(struct)

    def parse_doc(self, link: str, dump: bool = True):
        resp = requests.get(link, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://shimo.im/'})
        soup = BeautifulSoup(resp.content, 'lxml')
        editor = soup.find('div', class_='ql-editor')

        para: Paragraph | None = None
        parent: Paragraph | None = None
        sub_paras: list[Paragraph] | None = None

        for child in editor.children:
            text = child.text.strip()
            # print(text)

            match tag := tag_type(child):
                case Type.lead:
                    parent = Paragraph()
                    self.paras.append(parent)
                    para = parent
                    sub_paras = para.subs
                    para.lead = text
                case Type.sub_lead:
                    para = Paragraph()
                    sub_paras.append(para)
                    para.lead = text
                case Type.image if para:
                    para.img_url = self.upload_img(child.img['src'])
                case Type.body if para:  # if not para.subs and para.lead:
                    para.body.append(text)
                # case Type.body if para.subs and para.subs.lead:
                #     para.subs.body.append(text)

        if dump:
            with open('tmp/structs.yaml', 'w', encoding='utf-8') as stream:
                yaml.dump(self.paras, stream, allow_unicode=True)

    def render(self):
        with open('template.html', encoding='utf-8') as temp:
            template = Template(temp.read(), trim_blocks=True, lstrip_blocks=True)

            return template.render(title=self.title, author=self.author, paras=self.paras)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('-l', '--link', help='Original article link.')
    parser.add_argument('-s', '--structs', help='Parsed document structure file.')
    parser.add_argument('-c', '--content', help='Rendered rich media content file.')
    parser.add_argument('-d', '--draft', help='Wechat official account platform article draft media id.')
    args = parser.parse_args()

    with open('config.yaml', 'r', encoding='utf-8') as fin:
        article = WeArticle(config=yaml.safe_load(fin))

        if args.content:
            with open(args.content, encoding='utf-8') as html:
                res = html.read()
        else:
            if args.structs:
                article.load_structs(args.structs)
            else:
                article.parse_doc(args.link)

            res = article.render()

        if args.draft:
            article.update_draft(args.draft, res)  # 'gQOp_H1dB3TUt_Jiz4f-mgKQ5khPhm8sAlqAGnEH8FY'
        else:
            print('media_id: ', article.create_draft(res))

        with open('tmp/rich_media_content.html', 'w', encoding='utf-8') as fout:
            fout.write(res)
