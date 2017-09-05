#!/usr/bin/env python
# Scrape BBC Desert Island Discs data including songs, books, and luxury item,
# if available, for the celebrity "castaways".
#
# *based on original work by Francis Irving 
# *July 2012 - rewritten by Tom Morris with the following changes:
#  - updated to current BBC page format
#  - switched from BeautifulSoup to lxml
#  - updated deprecated database calls
#  - restructured to run as a single integrated process 
#    and not rescrape data it already extracted
# *May 2015 - ported to morph.io from dead ScraperWiki & updated to match
#  current BBC web site layout
#
# TODO:
# - get rid of dependency on scraperwiki package

from __future__ import print_function
import scraperwiki
import lxml.html
from lxml import etree
from datetime import datetime
from urllib2 import HTTPError

SITE = 'http://www.bbc.co.uk'
BASE = SITE + '/programmes/b006qnmr/episodes/guide'
INDEX_PAGE_SIZE = 30


raise NotImplementedError("BBC Radio 4 site changed format August 28, 2017 and this code has not yet been updated for new layout")

tables = scraperwiki.sqlite.show_tables()
past = []
if tables:
    for table in ['data','swdata']:
        if table in tables:
            past = [(i['date'],i['guest']) for i in scraperwiki.sqlite.select("* from " + table + " WHERE type == 'url'")]
            break

print('Database contains %d past entries' % len(past))

def get_broadcast_date(url):
    # We really want the first broadcast date, but with no Javascript it's on a second page
    url = url + '/broadcasts'

def process_music(segment, template, favorite):
    text = segment.cssselect('div.segment__track')[0]
    artist = text.cssselect('span.artist')
    names = [n.text_content().strip() for n in text.cssselect('span[property="name"]')]
    if artist:
        artist = artist[0].text_content().strip()
        track = names[1]
    else:
        artist = None
        track = names[0] # a guess for rare case
        print('Artist missing for : ' + etree.tostring(text))
        print ('Track: ', track.encode('utf-8'))
        print('Names: ', [n.encode('utf-8') for n in names])

    # extract artist musicbrainz id if available
    link = text.cssselect('h3 a') # need to parse link attribute url
    if link:
        mb_id = link[0].attrib['href'].split('/')[-1]
    else:
        mb_id = None

    performers = text.cssselect('span[property="contributor"]')

    principal = 'artist'
    composer = None
    # TODO: If we have a <span[property="contributor"> elements which 
    # are prefaced with "Performer: "
    # it's probably a classical piece where the "artist" is the composer
    if performers:
        principal = 'composer'
        composer = artist
        artist = None
        performer = performers[0]
        if 'Performer:' in performer.text_content():
            artist = performer.cssselect('span[property="name"]')[0].text_content().strip()

    rec = template.copy()
    # Favorites are called "keep" for historical reasons
    rec.update({'type': 'record_keep' if favorite else 'record',
               'title' : track,
               'performer' : artist,
               'composer' : composer,
               'principal' : principal,
               'mb_id' : mb_id,
               })
    scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

def process_segment(segment, template):
    # Favorite is contained in a group
    favorite = 'segments-list__group-items' in segment.getparent().attrib['class']

    if 'segments-list__item--music' in segment.attrib['class']:
        process_music(segment, template, favorite)
    elif 'segments-list__item--speech' in segment.attrib['class']:
        # FIXME: speech items can be used for spoken word discs as well as book/luxury
        # check head for correct type
        # test items missing books, not luxury items:
        #   http://www.bbc.co.uk/programmes/b03z3l2g
        #   http://www.bbc.co.uk/programmes/b037gm1f

        item_type = segment.cssselect('h3 span.title')
        if item_type:
            item_type = item_type[0].text_content().strip().lower()
            if item_type.startswith('book'):
                title = segment.cssselect('div.segment__content p')[0].text_content()
                rec = template.copy()
                rec.update({'type': 'book',
                            'title' : title,
                            })
                scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec)
            elif item_type.startswith('lux'):
                item_name = segment.cssselect('div.segment__content p')[0].text_content()
                rec = template.copy()
                rec.update({'type': 'luxury',
                            'title' : item_name,
                            })
                scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec)
            else:
                print('Unknown item _type: ' + item_type)
        else:
            print('Missing item type: ' + etree.tostring(segment))
    elif 'segments-list__item--group' not in segment.attrib['class']:
        print('Unknown segment class: ' + segment.attrib['class'])


def process_segments(url, template):
        # NOTE: Without Javascript, records picks are on a separate page
    try:
        seghtml = scraperwiki.scrape(url+'/segments').decode("utf-8")
    except HTTPError:
        print('Failed to fetch /segments for ', url)
        return False

    segroot = lxml.html.fromstring(seghtml)

    choices =  segroot.cssselect('li.segments-list__item--music')
    choices =  segroot.cssselect('li.segments-list__item')
    if len(choices) != 11: # 8 discs + book + luxury item + favorite marker
        print('Unexpected number of choices: ', len(choices))

    for choice in choices:
        process_segment(choice, template)

    # URL record must be written last because it's the key we use to determine record is complete
    rec = template.copy()
    rec.update({'type':'url',
                'title':url,
                })
    scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

def process_guest(name, url):

    try:
        html = scraperwiki.scrape(url).decode("utf-8")
    except HTTPError as e:
        print('Unable to fetch URL: ', url, e)
        return
    root = lxml.html.fromstring(html)
    title = root.cssselect('div.island div h1')
    
    # Check for unexpected page format
    if title == None or len(title) == 0:
        print('skipping, no <div "class=island"><H1>, page format has changed? ',url)
        return

    intro = root.cssselect('div.episode-panel__intro div.prose')

    date = root.cssselect('div.broadcast-event__time')[0].attrib['content']
    #date = datetime.strptime(date,'%d %b %Y').strftime('%Y-%m-%d')
    date = date[0:10]
        
    # TODO Can we dedupe on programme ID instead?
    if (date,name) in past:
        print('Skipping %s %s' % (name, date))
        return False
    else:
        print('Processing %s %s' % (name.encode('utf-8'), url))

    occupation = 'missing'

    # Denormalized schema, but that's a little easier for consumers
    # Old schema - Pass1: date date_scraped url guest
    # Pass2: date_scraped guest title url date type performer
    # old record types: record keep_record book luxury
    template = {'date_scraped' : datetime.now(),
                'date':date,
                'guest':name,}
    rec = template.copy()
    rec.update({'type':'occupation',
                'title':occupation,
                })
    scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

    castaway = title[0].text_content()
    if not castaway == name:
        print('Mismatched names between index (%s) and detail page (%s)' % (name,castaway))
        rec = {'date':date,
               'guest':name,
               'type':'alternate_name',
               'title':castaway,
               }
        scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

    # TODO It would be more efficient to only fetch the page once for all broadcasts, 
    # but we sacrifice a small amount of efficiency for the rare cast to better fit with our control flow

    broadcast_id = url.split('/')[-1]
    # TODO we used to handle multiple broadcasts per page, but
    # not sure what the current structure is

    process_segments(url, template)

    return True

def process_index_page(pg):
    items = pg.cssselect('#programmes-main-content > div.br-box-page.programmes-page > div > ol > li')
    if len(items) != INDEX_PAGE_SIZE:
        print('**Index page has %d items, expected %d' % (len(items), INDEX_PAGE_SIZE))
    count = 0
    for item in items:
        program = item.cssselect('div.programme__body')
        if not program:
            print('Unable to process item - program body div')
            continue
        program = program[0]
        guest = program.cssselect('h2.programme__titles a')
        if not guest:
            print('Unabled to find guest name')
            continue
        guest = guest[0]
        guest_url = SITE + guest.attrib['href']
        guest_name = guest.text_content().strip()
        if process_guest(guest_name, guest_url):
            count += 1
    print('Processed %d of %d shows' % (count,len(items)))
    return count

def fetch_index_page(page_num):
    print('Fetching index page %d' % page_num)
    page_html = scraperwiki.scrape(BASE + '?page=' + str(page_num))
    return lxml.html.fromstring(page_html)
        
def main():
    try:
        index_html = scraperwiki.scrape(BASE).decode("utf-8")
    except HTTPError as e:
        print("Unabled to fetch " + BASE)
        raise e
    index = lxml.html.fromstring(index_html)
    # TODO: use attribute instead to make more reliable
    #episode_count = int(index.cssselect('p#did-search-found').get('data-total'))
    last_index_page = int(index.cssselect('li.pagination__page.pagination__page--last')[0].text_content())
    print('Found %d index pages' % last_index_page)
    
    count = process_index_page(index) # handle the first page
    for page_num in range(2,last_index_page+1):
        page = fetch_index_page(page_num)
        count += process_index_page(page)
    print('Processed %d new entries' % count)

def test():
    # Test multiple appearances
    print(process_guest('1980-12-20','Arthur Askey','Comedian, Music hall','http://www.bbc.co.uk/radio4/features/desert-island-discs/castaway/663e79cf#p009mvl6'))
    print(process_guest('1942-04-02','Arthur Askey','Comedian','http://www.bbc.co.uk/radio4/features/desert-island-discs/castaway/663e79cf#p009y0mc'))
    # Test index pages without dates
    page = fetch_index_page(96)
    process_index_page(page)

main()
#test()
