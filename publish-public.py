import sys, re, tempfile, os, shutil, datetime, json, urllib, base64, subprocess
import xmlrpclib
from mutagen.easyid3 import EasyID3, EasyID3KeyError
from mutagen.oggvorbis import OggVorbis
from mutagen.mp3 import MP3
import mutagen.id3
from mutagen.flac import Picture;
import requests
import httplib
import socket
import StringIO
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup
import upload_video

hostname = socket.gethostname()
if hostname == "lugradio.vm.bytemark.co.uk":
    LIVE = True
else:
    LIVE = False

import dropbox
access_token = "hahano"
client = dropbox.client.DropboxClient(access_token)

# api key created in discourse admin. probably super-secret, so don't tell anyone.
APIKEY="nochance"
APIUSERNAME="sil"
QSPARAMS = {"api_key": APIKEY, "api_username": APIUSERNAME}
FORUM = "http://community.badvoltage.org/"

WP_BLOG_ID = 1
WP_AUTHOR_ID = 2
WP_AUTHOR_USER = "no"
WP_AUTHOR_PASS = "no"
BLOCKSIZE = 1024 * 64

DEFAULT_FORUM_LINK_TEXT = "Discuss this show in the community!"
DEFAULT_POST_LINK_TEXT = "Download the show now!"

DRY_RUN = False

def show_available_shows():
    folder_metadata = client.metadata('/badvoltageshows')
    available = {}
    for path in [x["path"] for x in folder_metadata["contents"]]:
        filename = path.split("/")[-1]
        m = re.match("^Bad Voltage ([0-9]+x[0-9][0-9]).*", filename)
        if m:
            available[m.groups()[0]] = ""
    if not available:
        print "There seem to be no shows available."
        print "Shows should be named like 'Bad Voltage 1x01.mp3'."
    else:
        print "Available shows:", ", ".join(sorted(available.keys()))
        print "Rerun this script as '%s %s \"default\" \"default\"'" % (sys.argv[0], sorted(available.keys())[-1])
        print ("The two parameters after the show name are the text for "
               "the link to the forum post (on the Wordpress post) and "
               "the link to the Wordpress post (on the forum post).")
        print ("If you specify those parameters as 'default' then the default "
               "text will be used; currently that is:")
        print "On the badvoltage.org Wordpress post, it will say: '%s'" % DEFAULT_FORUM_LINK_TEXT
        print "On the community.badvoltage.org forum post, it will say: '%s'" % DEFAULT_POST_LINK_TEXT

def check_show_specified():
    global DRY_RUN
    print "First, let's check that you specified a show."
    if len(sys.argv) < 4:
        show_available_shows()
        sys.exit(1)
    if not re.match("^[0-9]+x[0-9][0-9]$", sys.argv[1]):
        show_available_shows()
        sys.exit(1)
    forum_link_text = DEFAULT_FORUM_LINK_TEXT
    post_link_text = DEFAULT_POST_LINK_TEXT
    if sys.argv[2].lower() != "default":
        forum_link_text = sys.argv[2]
    if sys.argv[3].lower() != "default":
        post_link_text = sys.argv[3]
    if len(sys.argv) > 4 and sys.argv[4] == "DRY_RUN":
        DRY_RUN = True
        print "Dry run mode turned on"
    return (sys.argv[1], forum_link_text, post_link_text)

def check_formats_available(show_id):
    print "OK, you specified show %s. Does it have ogg and mp3 versions and notes available?" % show_id
    folder_metadata = client.metadata('/badvoltageshows')
    SHOW = "/badvoltageshows/Bad Voltage %s.%%s" % show_id
    ogg = [x["path"] for x in folder_metadata["contents"] if x["path"] == (SHOW % "ogg")]
    mp3 = [x["path"] for x in folder_metadata["contents"] if x["path"] == (SHOW % "mp3")]
    notes = [x["path"] for x in folder_metadata["contents"] if x["path"] == (SHOW % "notes")]
    #print [x["path"] for x in folder_metadata["contents"]]
    if (len(ogg) == 1) and (len(mp3) == 1) and (len(notes) == 1):
        print "Yes, it does."
        return (mp3[0], ogg[0], notes[0])
    else:
        if (len(ogg) == 0):
            print "I couldn't find an ogg file named %s, so bailing." % (SHOW % "ogg")
            sys.exit(0)
        elif (len(mp3) == 0):
            print "I couldn't find an mp3 file named %s, so bailing." % (SHOW % "mp3")
            sys.exit(0)
        elif (len(notes) == 0):
            print "I couldn't find an notes file named %s, so bailing." % (SHOW % "notes")
            sys.exit(0)
        elif (len(ogg) > 1):
            print "I found multiple ogg files (%s). Confused, so bailing." % (", ".join(ogg))
            sys.exit(0)
        elif (len(mp3) > 1):
            print "I found multiple mp3 files (%s). Confused, so bailing." % (", ".join(mp3))
            sys.exit(0)
        elif (len(notes) > 1):
            print "I found multiple notes files (%s). Confused, so bailing." % (", ".join(notes))
            sys.exit(0)
        else:
            print "Something odd happened, so bailing."
            sys.exit(0)

def fetch_file(dropboxf, metadata):
    size = float(metadata["bytes"])
    (handle, local_fn) = tempfile.mkstemp()
    out = open(local_fn, 'w')
    downloaded = 0
    block = dropboxf.read(BLOCKSIZE)
    sys.stdout.write("Downloaded: 0%")
    while True:
        out.write(block)
        downloaded += BLOCKSIZE
        block = dropboxf.read(BLOCKSIZE)
        pc = 100 * downloaded / size
        if pc > 100: pc = 100
        sys.stdout.write("\rDownloaded: %02d%%" % (pc))
        if not block: break
    out.close()
    print
    return local_fn

def compute_metadata(show_id, show_title):
    audio_metadata = {
        "title": show_id + ": " + show_title,
        "artist": "Bad Voltage", 
        "genre": "Vocal",
        "album": "Season " + show_id.split("x")[0],
        "tracknumber": show_id.split("x")[1],
        "date": unicode(datetime.datetime.now().year),
        "bv_override_title": show_title
    }
    return audio_metadata

def download_and_fix_cover_image(url):
    filename, headers = urllib.urlretrieve(url)
    print "Fetched cover to", filename
    return filename

def download_and_fix_mp3(mp3, audio_metadata, cover_art_file):
    global DRY_RUN
    if DRY_RUN:
        print "This is a dry run. So pretending to download the mp3..."
        return "/tmp/mp3"
    print "Now downloading the mp3 in order to set the metadata in it..."
    if not LIVE and len(sys.argv) >= 5 and os.path.exists(sys.argv[4]):
        mp3_local_fn = sys.argv[4]
        print "(using presupplied file %s)" % mp3_local_fn
    else:
        f, metadata = client.get_file_and_metadata(mp3)
        mp3_local_fn = fetch_file(f, metadata)
    print "Successfully downloaded (to %s): now editing metadata..." % mp3_local_fn
    try:
        audio = EasyID3(mp3_local_fn)
    except mutagen.id3.error:
        audio = EasyID3()
        audio.save(mp3_local_fn)
    for k in audio_metadata.keys():
        try:
            audio[k] = audio_metadata[k]
        except EasyID3KeyError:
            if not LIVE:
                print "%s is an invalid ID3 key, but we don't mind" % k
    audio.save(mp3_local_fn)
    # now re-open it in hardcore id3 mode to save the cover art
    audio=MP3(mp3_local_fn,ID3=mutagen.id3.ID3);
    audio.tags.add(mutagen.id3.APIC(encoding=3, mime='image/jpeg', type=3, 
    desc=u'Cover', data=open(cover_art_file).read()));
    audio.save()
    return mp3_local_fn

    audio_metadata = dict([(x,audio[x]) for x in 
        ["title", "artist", "genre", "album", "tracknumber", "date"]])
    if len(audio_metadata["title"]) != 1:
        print ("The mp3 title seems to be wrong. I'm expecting it to be "
            "one item, but instead it is %r") % (audio_metadata["title"],)
        print "This needs correcting. Abort."
        sys.exit(1)
    if ":" not in audio_metadata["title"][0]:
        print ("The mp3 title seems to be wrong. I'm expecting it to be "
            "of the form 'show number: Show Title', but instead it is %s") % (audio_metadata["title"][0],)
        print "This needs correcting. Abort."
        sys.exit(1)
    check_title = audio_metadata["title"][0].split(":",1)
    audio_metadata["bv_override_title"] = check_title[1]
    return mp3_local_fn

def download_and_fix_ogg(ogg, audio_metadata, cover_art_file):
    global DRY_RUN
    if DRY_RUN:
        print "This is a dry run. So pretending to download the ogg..."
        return "/tmp/ogg"
    print "Now downloading the ogg in order to set the metadata in it..."
    if not LIVE and len(sys.argv) >= 6 and os.path.exists(sys.argv[5]):
        ogg_local_fn = sys.argv[5]
        print "(using presupplied file %s)" % ogg_local_fn
    else:
        f, metadata = client.get_file_and_metadata(ogg)
        ogg_local_fn = fetch_file(f, metadata)
    print "Successfully downloaded (to %s): now editing metadata..." % ogg_local_fn
    audio = OggVorbis(ogg_local_fn)
    for k in audio_metadata.keys():
        audio[k] = audio_metadata[k]
    # add cover art
    im=Image.open(cover_art_file)
    w,h=im.size
    p=Picture()
    imdata=open(cover_art_file,'rb').read()
    p.data=imdata
    p.type=3
    p.desc=''
    p.mime='image/jpeg';
    p.width=w; p.height=h
    p.depth=24
    dt=p.write(); 
    enc=base64.b64encode(dt).decode('ascii');
    audio['metadata_block_picture']=[enc];
    audio.save()
    print "Successfully updated metadata."
    return ogg_local_fn

def check_notes_valid(notes):
    print "Now downloading the notes to confirm they're valid..."
    f, metadata = client.get_file_and_metadata(notes)
    notes_local_fn = fetch_file(f, metadata)
    fp = open(notes_local_fn)
    data = fp.read()
    fp.close()
    if "[display_podcast]" not in data:
        print "Your post does not contain anywhere to display the podcast player."
        print "Edit it to contain [display_podcast] somewhere."
        sys.exit()
    if "[forum_post_link]" not in data:
        print "Your post does not contain anywhere to put the forum/post link."
        print "Edit it to contain [forum_post_link] somewhere."
        sys.exit()
    out = []
    title = None
    headerimage = None
    categories = []
    for line in data.split("\n"):
        m1 = re.match(r'^title: ?(.*)$', line)
        m2 = re.match(r'^headerimage: ?(.*)$', line)
        m3 = re.match(r'^categories: ?(.*)$', line)
        if not m1 and not m2 and not m3:
            out.append(line)
        elif m1:
            title = m1.groups()[0]
        elif m2:
            headerimage = m2.groups()[0]
        elif m3:
            bits = m3.groups()[0].split(",")
            bits = [x.strip().split(None, 1) for x in bits]
            categories = [x[1] for x in bits if x[0] == "yes"]
    if not title:
        print "Your post does not contain a line which looks like 'title: Show title'."
        print "Edit it to contain such a line."
        sys.exit()
    if not headerimage:
        print "Your post does not contain a line which looks like 'headerimage: http://example.com/whatever.jpg'."
        print "Edit it to contain such a line."
        sys.exit()
    if not categories:
        print "Your post does not contain a line which looks like 'categories: yes Shows, no Linux'."
        print "Edit it to contain such a line."
        sys.exit()
    return title, headerimage, categories, notes_local_fn, "\n".join(out)

def re_upload_ogg(dropboxogg, ogg_file):
    if not LIVE:
        print "Not re-uploading the ogg, because we're not live."
        return
    print "Now re-uploading the Ogg, which now has metadata, to Dropbox."
    f = open(ogg_file)
    response = client.put_file(dropboxogg, f, overwrite=True)
    print "Done."

def delete_downloaded_files(mp3_file, ogg_file, notes_file, video_file, poster):
    print "Now tidying up by deleting the downloaded files."
    if not LIVE:
        print "*** This is not running on the live server, so skipping the delete-files step"
        return
    os.remove(mp3_file)
    os.remove(ogg_file)
    os.remove(notes_file)
    os.remove(video_file)
    os.remove(poster)

def move_files_to_downloadable_location(show_id, mp3_file, ogg_file):
    base_url = "http://audio.lugradio.org/badvoltage/Bad Voltage %s.%s"
    if not LIVE:
        print "*** This is not running on the live server, so skipping the move-files step"
        print "*** The downloaded and fixed files (for checking) are:"
        print "*** mp3: %s, ogg: %s" % (mp3_file, ogg_file)
        return (base_url % (show_id, "mp3"), base_url % (show_id, "ogg"))
    shutil.copyfile(mp3_file, "/var/www/audio.lugradio.org/badvoltage/Bad Voltage %s.mp3" % show_id)
    shutil.copyfile(ogg_file, "/var/www/audio.lugradio.org/badvoltage/Bad Voltage %s.ogg" % show_id)
    return (base_url % (show_id, "mp3"), base_url % (show_id, "ogg"))

def create_discourse_via_api(notes, 
      mp3_file, ogg_file, mp3_url, ogg_url, audio_metadata, 
      show_id, forum_link_text, post_link_text, header_img_data):
    print "Posting to Discourse forum...",
    # First, get cookie
    r = requests.get(FORUM, params=QSPARAMS)
    SESSION_COOKIE = r.cookies["_forum_session"]
    # Now, send a post to the _forum_session
    # work out the wordpress link, by guessing
    d = datetime.datetime.now()
    wordpress_link = "http://www.badvoltage.org/%s/%02d/%02d/%s" % (
      d.year, d.month, d.day, show_id)
    post_body = notes.replace("[forum_post_link]", 
          '<a href="%s">%s</a>' % (wordpress_link, post_link_text)).replace(
          "[display_podcast]",""),
    post_details = {
        "title": "%s: %s" % (show_id, audio_metadata["bv_override_title"]),
        "raw": post_body,
        "category": 7, # show feedback
        "archetype": "regular",
        "reply_to_post_number": 0
    }

    if not LIVE:
        print "\n*** This is not running on the live server, so skipping the discourse post"
        print "The posted data would have been:"
        print post_details
        return "http://not-a-discourse-link"

    r = requests.post(FORUM + "posts", params=QSPARAMS, data=post_details, 
        cookies={"_forum_session": SESSION_COOKIE})

    disc_data = json.loads(r.text)
    try:
        discourse_link = "http://community.badvoltage.org/t/%(topic_slug)s/%(topic_id)s" % disc_data
    except:
        print "Failed to post to Discourse with an error:"
        print disc_data
        sys.exit(1)
    print "done. (%s)" % (discourse_link,)
    return discourse_link


def create_wordpress_via_api(notes,
      mp3_file, ogg_file, mp3_url, ogg_url, audio_metadata,
      show_id, forum_link_text, post_link_text, discourse_link,
      header_img_data, requested_categories, youtube_link):
    # First, get audio duration and file size
    global DRY_RUN
    if DRY_RUN:
        print "Using fake data for WordPress post"
        duration_sec = 3601
        duration_min = duration_sec / 60
        duration_m = int(duration_min)
        duration_s = int((duration_min - duration_m) * 60)
        duration = "%s:%02d" % (duration_m, duration_s)
        ogg_size = 50000000
        mp3_size = 60000000
    else:
        aud = MP3(mp3_file)
        duration_sec = aud.info.length
        duration_min = duration_sec / 60
        duration_m = int(duration_min)
        duration_s = int((duration_min - duration_m) * 60)
        duration = "%s:%02d" % (duration_m, duration_s)
        ogg_size = os.path.getsize(ogg_file)
        mp3_size = os.path.getsize(mp3_file)

    # Now, assemble all our data
    post_contents = {
        "post_type": "post",
        "post_title": "%s: %s" % (show_id, audio_metadata["bv_override_title"]),
        "post_author": WP_AUTHOR_ID,
        "post_content": notes.replace("[forum_post_link]", 
          u'<a class="community-button" href="%s">%s</a>' % (discourse_link, forum_link_text)).replace(
          '[display_podcast]', youtube_link + '\n[display_podcast]'),
        "post_status": "draft",
        "comment_status": "closed",
        "post_name": show_id
    }
    if LIVE:
        post_contents["post_status"] = "publish"
    podcast_files = [
        {
            "title": "BV %s mp3" % show_id,
            "URI": mp3_url,
            "size": mp3_size,
            "duration": duration,
            "type": "audio_mp3"
        },
        {
            "title": "BV %s ogg" % show_id,
            "URI": ogg_url,
            "size": ogg_size,
            "duration": duration,
            "type": "audio_ogg"
        }
    ]

    generic_post_data = {
        'dimensionW': 0, 'atom': 'on', 
        'previewImage': 'http://badvoltage.org/wp-content/plugins/podpress/images/vpreview_center.png', 
        'rss': 'on', 'dimensionH': 0
    }

    podcast_file_data = []
    for p in podcast_files:
        d = {}
        d.update(generic_post_data)
        d.update(p)
        # set Include In: RSS on for mp3s, off for everything else
        if p['type'] != 'audio_mp3': del d['rss']
        podcast_file_data.append(d)

    s=xmlrpclib.Server("http://badvoltage.org/xmlrpc.php")
    print "Fetching the taxonomy list to put this post in 'Shows'..."
    categories_as_term_list = [x for x in 
        s.wp.getTerms(WP_BLOG_ID, WP_AUTHOR_USER, WP_AUTHOR_PASS, 'category')
        if x["name"] in requested_categories]
    post_contents["terms"] = {"category": [x["term_id"] for x in categories_as_term_list]}

    if not LIVE:
        print "*** This is not running on the live server, so skipping the WP post"
        print "The WP post data would have been"
        print post_contents
        return

    sio = StringIO.StringIO()
    header_img_data.save(sio, format="JPEG")
    img_data = {
        "name": "img_%s.jpg" % (show_id,),
        "type": "image/jpeg",
        "bits": xmlrpclib.Binary(sio.getvalue()),
        "overwrite": True
    }

    print "Uploading image to WordPress..."
    image_upload = s.wp.uploadFile(WP_BLOG_ID, WP_AUTHOR_USER, WP_AUTHOR_PASS, img_data)
    print "Creating a post on badvoltage.org..."
    post_contents["post_thumbnail"] = image_upload["id"]
    postid = s.wp.newPost(WP_BLOG_ID, WP_AUTHOR_USER, WP_AUTHOR_PASS, post_contents)
    print "...and adding podcast media to it..."
    s.podPress.setPostData(postid, podcast_file_data)
    print "...done."

def create_youtube_via_api(notes, video_file, show_id, audio_metadata, discourse_link):
    print "Uploading to YouTube..."
    soup = BeautifulSoup(notes)
    description = soup.get_text().replace("[display_podcast]", "").replace(
        "[forum_post_link]", "Share your thoughts on the show at %s" % discourse_link)

    tags = None
    body=dict(
        snippet=dict(
            title="Bad Voltage " + audio_metadata["title"],
            description=description,
            tags=tags,
            categoryId=28 # science and tech
        ),
        status=dict(
            privacyStatus="public" # can be "public"
        )
    )

    if not LIVE:
        print "\n*** This is not running on the live server, so skipping the YouTube upload"
        print "The posted data would have been:"
        print body
        return

    # Call the API's videos.insert method to create and upload the video.
    from oauth2client.tools import argparser
    args = argparser.parse_args('')
    youtube = upload_video.get_authenticated_service(args)
    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        # The chunksize parameter specifies the size of each chunk of data, in
        # bytes, that will be uploaded at a time. Set a higher value for
        # reliable connections as fewer chunks lead to faster uploads. Set a lower
        # value for better recovery on less reliable connections.
        #
        # Setting "chunksize" equal to -1 in the code below means that the entire
        # file will be uploaded in a single HTTP request. (If the upload fails,
        # it will still be retried where it left off.) This is usually a best
        # practice, but if you're using Python older than 2.6 or if you're
        # running on App Engine, you should set the chunksize to something like
        # 1024 * 1024 (1 megabyte).
        media_body=upload_video.MediaFileUpload(video_file, chunksize=1024*1024, resumable=True)
    )
    youtube_id = upload_video.resumable_upload(insert_request)
    return ('<iframe width="560" height="315" '
            'src="https://www.youtube.com/embed/%s" '
            'frameborder="0" allowfullscreen></iframe>') % youtube_id

def create_post_via_api(notes_file, mp3_file, ogg_file, mp3_url, ogg_url, 
  audio_metadata, show_id, forum_link_text, post_link_text, 
  header_img_data, categories, video_file):
    discourse_link = create_discourse_via_api(notes_file, 
      mp3_file, ogg_file, mp3_url, ogg_url, audio_metadata, 
      show_id, forum_link_text, post_link_text, header_img_data)
    youtube_link = create_youtube_via_api(notes_file,
        video_file, show_id, audio_metadata, discourse_link)
    create_wordpress_via_api(notes_file, 
      mp3_file, ogg_file, mp3_url, ogg_url, audio_metadata, 
      show_id, forum_link_text, post_link_text, discourse_link, 
      header_img_data, categories, youtube_link)

def download_and_fix_image(headerimageurl):
    response = requests.get(headerimageurl)
    img = Image.open(StringIO.StringIO(response.content))
    # header image needs to be 960x600
    if img.size[0] < 960 or img.size[1] < 600:
        print "Sorry, the header image needs to be 960x600 or larger"
        sys.exit(1)
    scale_x_down_by = img.size[0] / 960.0
    scale_y_down_by = img.size[1] / 600.0
    if scale_x_down_by > scale_y_down_by:
        resize_to = (int(img.size[0] / scale_y_down_by), 600)
        x_border = int(resize_to[0]) - 960
        crop = (x_border / 2, 0, 960 + (x_border / 2), 600)
    else:
        resize_to = (960, int(img.size[1] / scale_x_down_by))
        y_border = int(resize_to[1]) - 600
        crop = (0, y_border / 2, 960, 600 + (y_border / 2))
    img.thumbnail(resize_to, Image.ANTIALIAS)
    cropped = img.crop(crop)
    return cropped

def create_video_from_mp3_and_poster(mp3, poster):
    if not LIVE and len(sys.argv) >= 7 and os.path.exists(sys.argv[6]):
        outputfile = sys.argv[6]
        print "(using presupplied file %s)" % outputfile
        return outputfile
    try:
        subprocess.check_call(["ffmpeg", "-loglevel", "0", "-nostats"])
    except OSError:
        program_name = "avconv"
    except:
        # ffmpeg throws non-zero exit status
        program_name = "ffmpeg"
    outputfile = "output.mkv"
    # https://trac.ffmpeg.org/wiki/Encode/YouTube but changed
    cmd = [program_name, "-y", "-loop", "1", "-framerate", "2", 
        "-i", poster, "-i", mp3, "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
        "-crf", "18", "-c:a", "aac", "-strict", "experimental", "-shortest", "-pix_fmt", "yuv420p",
        outputfile
    ]
    #print "Executing video encode command", cmd
    print "Encoding video..."
    subprocess.check_call(cmd)
    return outputfile

def create_poster(pilimg, show_id, show_title):
    # make image 854x480, which means more cropping
    # it is currently 960x600
    bgimg = pilimg.copy()
    bgimg.thumbnail((854, 534), Image.ANTIALIAS)
    cropped = bgimg.crop((0,(534-480)/2,854,534-((534-480)/2)))
    draw = ImageDraw.Draw(cropped)
    draw.rectangle(((0,350),(854,480)), fill="black")
    font = ImageFont.truetype("DIRTYEGO.TTF", 70)
    w,h = font.getsize("BAD VOLTAGE")
    textstart = (854 - w) / 2
    draw.text((textstart, 350), "BAD VOLTAGE", fill="white", font=font)
    draw.line(((textstart, 423),(854-textstart, 423)), fill="white")

    fontsize = 50
    namestr = "EPISODE " + show_id.split("x")[1] + ": " + show_title.upper()
    while True:
        font = ImageFont.truetype("DIRTYEGO.TTF", fontsize)
        w,h = font.getsize(namestr)
        if w < 854 - 50: break
        fontsize -= 1
        if fontsize == 1:
            font = ImageFont.truetype("DIRTYEGO.TTF", 40)
            namestr = "EPISODE " + show_id.split("x")[1]
    
    draw.text(((854-w)/2, 430), namestr, fill="white", font=font)
    del draw

    cropped.save("poster.png")
    return "poster.png"

def main():
    print """This is the Bad Voltage publish script.
    It does the following:
    1. check that you specified which show you want to do, and whine if you didn't
    2. confirm that there is both an mp3 and an ogg version of that show, and notes
    3. download the mp3 and extract the id3 data from it
    4. download the ogg, edit it to contain the same id3 data, and re-upload it
    5. cp the ogg and the mp3 to audio.lugradio.org in an appropriate location
    6. work out URLs for both the ogg and the mp3
    7. take the show notes you've written and check them for various things
    8. use the Wordpress API on badvoltage.org to create a post for the show
    9. use the Discourse API on community.bv.o to create a post for the show
    10. set the post to be published at a certain time
    11. clean up by deleting the local copies of files
    """

    show_id, forum_link_text, post_link_text = check_show_specified()
    mp3, ogg, notes = check_formats_available(show_id)
    show_title, headerimage, categories, notes_file, notes_content = check_notes_valid(notes)
    downloaded_header_image_pil = download_and_fix_image(headerimage)
    downloaded_cover_image = download_and_fix_cover_image("http://farm4.staticflickr.com/3794/10457827766_59715d2694_o.jpg")
    audio_metadata = compute_metadata(show_id, show_title)
    poster = create_poster(downloaded_header_image_pil, show_id, show_title)
    mp3_file = download_and_fix_mp3(mp3, audio_metadata, downloaded_cover_image)
    ogg_file = download_and_fix_ogg(ogg, audio_metadata, downloaded_cover_image)
    video_file = create_video_from_mp3_and_poster(mp3_file, poster)
    mp3_url, ogg_url = move_files_to_downloadable_location(show_id, mp3_file, ogg_file)
    create_post_via_api(notes_content, mp3_file, ogg_file, mp3_url, ogg_url, 
        audio_metadata, show_id, forum_link_text, post_link_text, 
        downloaded_header_image_pil, categories, video_file)
    delete_downloaded_files(mp3_file, ogg_file, notes_file, video_file, poster)
    print "Complete."
    print "Now do the following things, immediately:"
    print ("1. Confirm that the post is live on badvoltage.org, and that the mp3 and "
        "ogg linked from it both play.")
    print "2. Confirm that the post links correctly to the new forum thread."
    print "3. Confirm that the forum thread links correctly to the post."
    print "And you're done."

if __name__ == "__main__":
    main()

