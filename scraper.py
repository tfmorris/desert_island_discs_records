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

import scraperwiki
import lxml.html
from lxml import etree
from datetime import datetime
from urllib2 import HTTPError

SITE = 'http://www.bbc.co.uk'
BASE = SITE + '/radio4/features/desert-island-discs/find-a-castaway'
INDEX_PAGE_SIZE = 20

if scraperwiki.sqlite.show_tables():
    past = [(i['date'],i['guest']) for i in scraperwiki.sqlite.select("* from data WHERE type == 'url'")]
else:
    past = []
print 'Database contains %d past entries' % len(past)

def process_guest(date, name, occupation, url):
    if (date,name) in past:
        #print 'Skipping %s %s' % (date,name)
        return False

    try:
        html = scraperwiki.scrape(url).decode("utf-8")
    except HTTPError as e:
        print e
        print 'Unable to fetch URL: ', url
        return
    root = lxml.html.fromstring(html)
    intro = root.cssselect('div.island div h1')

    # Check for unexpected page format
    if intro == None:
        print 'skipping, no <div "class=island"><H1>, page format has changed? ',url
        return

    # Denormalized schema, but that's a little easier for consumers
    # Old schema - Pass1: date date_scraped url guest
    # Pass2: date_scraped guest title url date type performer
    # old record types: record keep_record book luxury
    rec = {'date_scraped' : datetime.now(),
           'date':date,
           'guest':name,
           'type':'occupation',
           'title':occupation,
           }
    scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

    castaway = intro[0].text_content()
    if not castaway == name:
        print 'Mismatched names between index (%s) and detail page (%s)' % (name,castaway)
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

    # NOTE: Without Javascript, records picks are on a separate page
    try:
        seghtml = scraperwiki.scrape(url+'/segments').decode("utf-8")
    except HTTPError:
        print 'Failed to fetch /segments for ', url
        return False

    segroot = lxml.html.fromstring(seghtml)

    choices =  segroot.cssselect('li.segments-list__item--music')
    if len(choices) != 8:
        print 'Unexpected number of choices: ', len(choices)

    for choice in choices:
        text = choice.cssselect('div.segment__track')[0]

        # TODO: Favorite is in separate group now
        #keep = text.cssselect('p.track_keep') # Only present if it's their favorite track
        keep = False
        artist = text.cssselect('span.artist')
        names = text.cssselect('span[property="name"]')
        if artist:
            artist = artist[0].text_content().strip()
            track = names[1].text_content().strip()
        else:
            artist = None
            track = names[0].text_content().strip() # a guess for rare case
            print 'Artist missing for selection on: ', url + '/segments'
            print 'Track: ', track.encode('utf-8'), ' names: ', names

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

        rec.update({'type': 'record_keep' if keep else 'record',
                    'title' : track,
                    'performer' : artist,
                    'composer' : composer,
                    'principal' : principal,
                    'mb_id' : mb_id,
                    })
        scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 

    # Clear music specific fields
    rec.update({'performer' : None,
                'composer' : None,
                'principal' : None,
                'mb_id' : None,
                 })

    nonmusic = segroot.cssselect('li.segments-list__item--speech')
    # DANGER - we assume fixed order for book & luxury item which will give
    # wrong result if the book is missing or the order is changed
    # They're not semantically tagged, but we could check the preceding <h3>
    # for BOOK CHOICE or LUXURY CHOICE options
    if len(nonmusic) > 0:
        title = nonmusic[0].cssselect('p')[0].text_content()
        rec.update({'type': 'book',
                    'title' : title,
                    })
        #print 'Book: ', title
        scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec)
    else:
        print 'Book missing for: ', url

    if len(nonmusic) > 1:
        item = nonmusic[1].cssselect('p')[0].text_content()
        rec.update({'type': 'luxury',
                    'title' : item,
                    })
        #print 'Luxury: ', item
        scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec)
    else:
        print 'Luxury item missing for: ', url

    # URL record must be written last because it's the key we use to determine record is complete
    rec = {'date_scraped' : datetime.now(),
           'date':date,
           'guest':name,
           'type':'url',
           'title':url,
           }
    scraperwiki.sqlite.save(["date", "guest", "type", "title"], rec) 
    return True

def process_index_page(pg):
    items = pg.cssselect('div.did-search-item')
    print 'Index page has %d items' % len(items)
    count = 0
    for item in items:
        text = item.cssselect('div.did-text')
        if not text:
            print 'Unable to process item - no text div'
            continue
        text = text[0]
        guest = text.cssselect('h4 a')
        if not guest:
            print 'Unabled to find guest name'
            continue
        guest = guest[0]
        guest_url = guest.attrib['href']
        guest_name = guest.text_content().strip()
        date = text.cssselect('p.did-date')
        if not date:
            print 'Unable to find broadcast date for guest "%s"' % guest_name
            continue
        date = date[0].text_content().split('|')[1].strip()	
        # Convert date to ISO format
        date = datetime.strptime(date,'%d %b %Y').strftime('%Y-%m-%d')
        occupation = text.cssselect('p.did-castaways-known-for')
        if len(occupation) > 0:
            occupation = occupation[0].text_content()
        else:
            occupation = ''
        #print date, guest_name.encode('utf-8'), occupation, guest_url
        if process_guest(date, guest_name, occupation, guest_url):
            count += 1
    print 'Processed %d of %d shows' % (count,len(items))
    return count

def fetch_index_page(page_num):
    print 'Fetching index page %d' % page_num
    page_html = scraperwiki.scrape(BASE + '/page/' + str(page_num))
    return lxml.html.fromstring(page_html)
        
def main():
    index_html = scraperwiki.scrape(BASE).decode("utf-8")
    index = lxml.html.fromstring(index_html)
    # TODO: use attribute instead to make more reliable
    #episode_count = int(index.cssselect('p#did-search-found').get('data-total'))
    episode_count = int(index.cssselect('p#did-search-found span')[0].text_content().split(' ')[0])
    if episode_count <= 0:
        print 'No episodes: ',etree.tostring(index)
    assert episode_count > 0
    print '%d total episodes' % episode_count
    last_index_page = (episode_count + INDEX_PAGE_SIZE - 1) / INDEX_PAGE_SIZE
    print 'Computed %d index pages' % last_index_page
    
    count = process_index_page(index) # handle the first page
    for page_num in range(2,last_index_page+1):
        page = fetch_index_page(page_num)
        count += process_index_page(page)
    print 'Processed %d new entries' % count

def test():
    # Test multiple appearances
    print process_guest('1980-12-20','Arthur Askey','Comedian, Music hall','http://www.bbc.co.uk/radio4/features/desert-island-discs/castaway/663e79cf#p009mvl6')
    print process_guest('1942-04-02','Arthur Askey','Comedian','http://www.bbc.co.uk/radio4/features/desert-island-discs/castaway/663e79cf#p009y0mc')
    # Test index pages without dates
    page = fetch_index_page(96)
    process_index_page(page)

main()
#test()
