import datetime
from pprint import pprint
from optparse import make_option
from collections import defaultdict
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.core.mail import send_mail
import facebook
import logging
from ...models import \
    Facebook_Feed as Facebook_Feed_Model, \
    Facebook_Status as Facebook_Status_Model, \
    User_Token as User_Token_Model, \
    Facebook_Status_Attachment as Facebook_Status_Attachment_Model

FACEBOOK_API_VERSION = 'v2.0'

NUMBER_OF_TRIES_FOR_REQUEST = 3

LENGTH_OF_EMPTY_ATTACHMENT_JSON = 21

DEFAULT_STATUS_SELECT_LIMIT_FOR_INITIAL_RUN = 100
DEFAULT_STATUS_SELECT_LIMIT_FOR_REGULAR_RUN = 20


class Command(BaseCommand):
    args = '<feed_id>'
    help = 'Fetches a feed'
    option_initial = make_option('-i',
                                 '--initial',
                                 action='store_true',
                                 dest='initial',
                                 default=False,
                                 help='Flag initial runs fql query with limit of {0} instead of regular limit of {1}.'
                                 .format(DEFAULT_STATUS_SELECT_LIMIT_FOR_INITIAL_RUN,
                                         DEFAULT_STATUS_SELECT_LIMIT_FOR_REGULAR_RUN)
    )
    option_force_attachment_update = make_option('-a',
                                                 '--force-attachment-update',
                                                 action='store_true',
                                                 dest='force-attachment-update',
                                                 default=False,
                                                 help='Use this flag to force updating of status attachment'
    )
    option_list_helper = list()
    for x in BaseCommand.option_list:
        option_list_helper.append(x)
    option_list_helper.append(option_initial)
    option_list_helper.append(option_force_attachment_update)
    option_list = tuple(option_list_helper)

    graph = facebook.GraphAPI()

    def fetch_status_objects_from_feed(self, feed_id, post_number_limit):
        """
        Receives a feed_id for a facebook
        Returns a facebook-sdk fql query, with all status objects published by the page itself.
                """
        select_self_published_status_query = """
                SELECT
                    post_id, message,
                    created_time, like_info, comment_info,
                    share_count, updated_time, attachment,
                    type, parent_post_id, permalink, description
                FROM
                    stream
                WHERE
                    source_id IN ({0}) AND actor_id IN ({0})
                LIMIT {1}""".format(feed_id, post_number_limit)

        api_request_path = "{0}/posts".format(feed_id)
        args_for_request = {'limit': post_number_limit,
                            'version': FACEBOOK_API_VERSION,
                            'fields': "from, message, id, created_time, \
                             updated_time, type, link, caption, picture, description, name,\
                             status_type, story, story_tags ,object_id, properties, source, to, shares, \
                             likes.summary(true).limit(1), comments.summary(true).limit(1)"}

        try_number = 1
        while try_number <= NUMBER_OF_TRIES_FOR_REQUEST:
            try:
                return_statuses = self.graph.request(path=api_request_path, args=args_for_request)
                return return_statuses
            except:
                warning_msg = "Failed first attempt for feed #({0}) from FB API.".format(feed_id)
                logger = logging.getLogger('django')
                logger.warning(warning_msg)
                try_number += 1
        error_msg = "Failed three attempts for feed #({0}) from FB API.".format(feed_id)
        logger = logging.getLogger('django.request')
        logger.warning(error_msg)
        return []

    @staticmethod
    def insert_status_attachment(status, status_object_defaultdict):
        print 'insert attachment'
        attachment_defaultdict = defaultdict(str, status_object_defaultdict)
        attachment = Facebook_Status_Attachment_Model(
            status=status,
            name=attachment_defaultdict['name'],
            caption=attachment_defaultdict['caption'],
            description=attachment_defaultdict['description'],
            link=attachment_defaultdict['link'],
            facebook_object_id=attachment_defaultdict['object_id'],
            type=attachment_defaultdict['type'],
            picture=attachment_defaultdict['picture']
        )
        attachment.save()
        # add all media files related to attachment


    @staticmethod
    def update_status_attachment(attachment, status_object_defaultdict):
        print 'update attachment'
        attachment_defaultdict = defaultdict(str, status_object_defaultdict)
        if attachment_defaultdict['link']:
            # Update relevant attachment fields
            attachment.name = attachment_defaultdict['name']
            attachment.caption = attachment_defaultdict['caption']
            attachment.description = attachment_defaultdict['description']
            attachment.link = attachment_defaultdict['link']
            attachment.facebook_object_id = attachment_defaultdict['object_id']
            attachment.type = attachment_defaultdict['type']
            attachment.picture = attachment_defaultdict['picture']
            attachment.save()
        else:
        # if has no link field - then there's no attachment, and it must be deleted
            print 'deleting attachment'
            attachment.delete()

    def create_or_update_attachment(self, status, status_object_defaultdict):
        """
        If attachment exists, create or update all relevant fields.
        """
        print 'create_or_update attachment'
        if status_object_defaultdict['link']:
            attachment, created = Facebook_Status_Attachment_Model.objects.get_or_create(
                status=status)
            print 'I have an attachment. Created now: %s; Length of data in field link: %d; field picture: %d;  id: %s' % (
                created, len(status_object_defaultdict['link']), len(str(status_object_defaultdict['picture'])), status.status_id)
            self.update_status_attachment(attachment, status_object_defaultdict)
        else:
            print 'i don''t have an attachment; Link field: %s; Picture field: %s; id: %s' % (
                str(status_object_defaultdict['link']), str(status_object_defaultdict['picture']),  status.status_id)

    def insert_status_object_to_db(self, status_object, feed_id, options):
        """
        Receives a single status object as retrieved from facebook-sdk, an inserts the status
        to the db.
                """
        # Create a datetime object from int received in status_object
        current_time_of_update = datetime.datetime.strptime(status_object['updated_time'],
                                                            '%Y-%m-%dT%H:%M:%S+0000').replace(tzinfo=timezone.utc)

        def_dict_rec = lambda: defaultdict(def_dict_rec)

        status_object_defaultdict = defaultdict(lambda: None, status_object)
        if status_object_defaultdict['message']:
            message = status_object_defaultdict['message']
        else:
            message = ''
        if status_object_defaultdict['likes']:
            like_count = status_object_defaultdict['likes']['summary']['total_count']
        else:
            like_count = None
        if status_object_defaultdict['comments']:
            comment_count = status_object_defaultdict['comments']['summary']['total_count']
        else:
            comment_count = None
        if status_object_defaultdict['shares']:
            share_count = status_object_defaultdict['shares']['count']
        else:
            share_count = None
        if status_object_defaultdict['status_type']:
            type_of_status = status_object_defaultdict['status_type']
        else:
            type_of_status = None
        if status_object_defaultdict['story']:
            story = status_object_defaultdict['story']
        else:
            story = None
        if status_object_defaultdict['story_tags']:
            story_tags = status_object_defaultdict['story_tags']
        else:
            story_tags = None

        published = datetime.datetime.strptime(status_object_defaultdict['created_time'],
                                               '%Y-%m-%dT%H:%M:%S+0000').replace(tzinfo=timezone.utc)

        try:
            # If post_id already exists in DB
            status = Facebook_Status_Model.objects.get(status_id=status_object['id'])
            if status.updated < current_time_of_update:
                # If post_id exists but of earlier update time, fields are updated.
                print 'update status'
                status.content = message
                status.like_count = like_count
                status.comment_count = comment_count
                status.share_count = share_count
                status.status_type = type_of_status  # note that fb has type AND status_type fields, here is status_type
                status.updated = current_time_of_update
                status.story = story
                status.story_tags = story_tags
                # update attachment data
                self.create_or_update_attachment(status, status_object_defaultdict)
            elif options['force-attachment-update']:
                # force update of attachment, regardless of time
                status.save()
                self.create_or_update_attachment(status, status_object_defaultdict)
                # If post_id exists but of equal or later time (unlikely, but may happen), disregard
                # Should be an else here for this case but as it is, just disregard
        except Facebook_Status_Model.DoesNotExist:
            # If post_id does not exist at all, create it from data.
            print 'create status'
            status = Facebook_Status_Model(feed_id=feed_id,
                                           status_id=status_object['id'],
                                           content=message,
                                           like_count=like_count,
                                           comment_count=comment_count,
                                           share_count=share_count,
                                           published=published,
                                           updated=current_time_of_update,
                                           status_type=type_of_status,
                                           story=story,
                                           story_tags=story_tags)
            if status_object_defaultdict['link']:
                # There's an attachment
                status.save()
                self.insert_status_attachment(status, status_object_defaultdict)
        finally:
            # save status object.
            status.save()

    def get_feed_statuses(self, feed, post_number_limit):
        """
        Returns a Dict object of feed ID. and retrieved status objects.
                """
        if feed.feed_type == 'PP':
            # Set facebook graph access tokens as app access token
            self.graph.access_token = facebook.get_app_access_token(
                settings.FACEBOOK_APP_ID,
                settings.FACEBOOK_SECRET_KEY
            )
            return {'feed_id': feed.id,
                    'statuses': self.fetch_status_objects_from_feed(feed.vendor_id, post_number_limit)}

        else:  # feed_type == 'UP' - User Profile
            # Set facebook graph access token to user access token
            token = User_Token_Model.objects.filter(feeds__id=feed.id).order_by('-date_of_creation').first()
            if not token:
                print 'No Token found for User Profile %s' % feed
                return {'feed_id': feed.id, 'statuses': []}
            else:
                print 'using token by user_id: %s' % token.user_id
                self.graph.access_token = token.token
                return {'feed_id': feed.id,
                        'statuses': self.fetch_status_objects_from_feed(feed.vendor_id, post_number_limit)}

    def handle(self, *args, **options):
        """
        Executes fetchfeed manage.py command.
        Receives either one feed ID and retrieves Statuses for that feed,
        or no feed ID and therefore retrieves all Statuses for all the feeds.
        """
        feeds_statuses = []
        if options['initial']:
            post_number_limit = DEFAULT_STATUS_SELECT_LIMIT_FOR_INITIAL_RUN
        else:
            post_number_limit = DEFAULT_STATUS_SELECT_LIMIT_FOR_REGULAR_RUN
        print 'Variable post_number_limit set to: {0}.'.format(post_number_limit)

        list_of_feeds = list()
        # Case no args - fetch all feeds
        if len(args) == 0:
            list_of_feeds = [feed for feed in Facebook_Feed_Model.objects.all()]
        # Case arg exists - fetch feed by id supplied
        elif len(args) == 1:
            feed_id = int(args[0])
            try:
                feed = Facebook_Feed_Model.objects.get(pk=feed_id)
                list_of_feeds.append(feed)
            except Facebook_Feed_Model.DoesNotExist:
                warning_msg = "Feed #({0}) does not exist.".format(feed_id)
                logger = logging.getLogger('django')
                logger.warning(warning_msg)
                raise CommandError('Feed "%s" does not exist' % feed_id)
        # Case invalid args
        else:
            raise CommandError('Please enter a valid feed id')
        # Iterate over list_of_feeds
        for feed in list_of_feeds:
            self.stdout.write('Working on feed: {0}.'.format(feed.pk))
            feed_statuses = self.get_feed_statuses(feed, post_number_limit)
            self.stdout.write('Successfully fetched feed: {0}.'.format(feed.pk))
            if feed_statuses['statuses']:
                for status in feed_statuses['statuses']['data']:
                    self.insert_status_object_to_db(status, feed_statuses['feed_id'], options)
                self.stdout.write('Successfully written feed: {0}.'.format(feed.pk))
            else:
                self.stdout.write('No statuses retrieved for feed: {0}, {1}.'.format(feed.id, feed.vendor_id))
            info_msg = "Successfully updated feed: {0}.".format(feed.pk)
            logger = logging.getLogger('django')
            logger.info(info_msg)
        info_msg = "Successfully saved all statuses to db"
        logger = logging.getLogger('django')
        logger.info(info_msg)
        self.stdout.write('Successfully saved all statuses to db.')