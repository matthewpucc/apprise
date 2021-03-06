# -*- coding: utf-8 -*-
#
# Copyright (C) 2019 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import re

import hmac
import requests
from hashlib import sha256
from datetime import datetime
from collections import OrderedDict
from xml.etree import ElementTree

from .NotifyBase import NotifyBase
from .NotifyBase import HTTP_ERROR_MAP
from ..utils import compat_is_basestring

# Some Phone Number Detection
IS_PHONE_NO = re.compile(r'^\+?(?P<phone>[0-9\s)(+-]+)\s*$')

# Topic Detection
# Summary: 256 Characters max, only alpha/numeric plus underscore (_) and
#          dash (-) additionally allowed.
#
#   Soure: https://docs.aws.amazon.com/AWSSimpleQueueService/latest\
#                   /SQSDeveloperGuide/sqs-limits.html#limits-queues
#
# Allow a starting hashtag (#) specification to help eliminate possible
# ambiguity between a topic that is comprised of all digits and a phone number
IS_TOPIC = re.compile(r'^#?(?P<name>[A-Za-z0-9_-]+)\s*$')

# Used to break apart list of potential tags by their delimiter
# into a usable list.
LIST_DELIM = re.compile(r'[ \t\r\n,\\/]+')

# Because our AWS Access Key Secret contains slashes, we actually use the
# region as a delimiter. This is a bit hacky; but it's much easier than having
# users of this product search though this Access Key Secret and escape all
# of the forward slashes!
IS_REGION = re.compile(r'^\s*(?P<country>[a-z]{2})-'
                       r'(?P<area>[a-z]+)-(?P<no>[0-9]+)\s*$', re.I)

# Extend HTTP Error Messages
AWS_HTTP_ERROR_MAP = HTTP_ERROR_MAP.copy()
AWS_HTTP_ERROR_MAP.update({
    403: 'Unauthorized - Invalid Access/Secret Key Combination.',
})


class NotifySNS(NotifyBase):
    """
    A wrapper for AWS SNS (Amazon Simple Notification)
    """

    # The default descriptive name associated with the Notification
    service_name = 'AWS Simple Notification Service (SNS)'

    # The services URL
    service_url = 'https://aws.amazon.com/sns/'

    # The default secure protocol
    secure_protocol = 'sns'

    # A URL that takes you to the setup/help of the specific protocol
    setup_url = 'https://github.com/caronc/apprise/wiki/Notify_sns'

    # The maximum length of the body
    # Source: https://docs.aws.amazon.com/sns/latest/api/API_Publish.html
    body_maxlen = 140

    def __init__(self, access_key_id, secret_access_key, region_name,
                 recipients=None, **kwargs):
        """
        Initialize Notify AWS SNS Object
        """
        super(NotifySNS, self).__init__(**kwargs)

        if not access_key_id:
            raise TypeError(
                'An invalid AWS Access Key ID was specified.'
            )

        if not secret_access_key:
            raise TypeError(
                'An invalid AWS Secret Access Key was specified.'
            )

        if not (region_name and IS_REGION.match(region_name)):
            raise TypeError(
                'An invalid AWS Region was specified.'
            )

        # Initialize topic list
        self.topics = list()

        # Initialize numbers list
        self.phone = list()

        # Store our AWS API Key
        self.aws_access_key_id = access_key_id

        # Store our AWS API Secret Access key
        self.aws_secret_access_key = secret_access_key

        # Acquire our AWS Region Name:
        # eg. us-east-1, cn-north-1, us-west-2, ...
        self.aws_region_name = region_name

        # Set our notify_url based on our region
        self.notify_url = 'https://sns.{}.amazonaws.com/'\
            .format(self.aws_region_name)

        # AWS Service Details
        self.aws_service_name = 'sns'
        self.aws_canonical_uri = '/'

        # AWS Authentication Details
        self.aws_auth_version = 'AWS4'
        self.aws_auth_algorithm = 'AWS4-HMAC-SHA256'
        self.aws_auth_request = 'aws4_request'

        if recipients is None:
            recipients = []

        elif compat_is_basestring(recipients):
            recipients = [x for x in filter(bool, LIST_DELIM.split(
                recipients,
            ))]

        elif not isinstance(recipients, (set, tuple, list)):
            recipients = []

        # Validate recipients and drop bad ones:
        for recipient in recipients:
            result = IS_PHONE_NO.match(recipient)
            if result:
                # Further check our phone # for it's digit count
                # if it's less than 10, then we can assume it's
                # a poorly specified phone no and spit a warning
                result = ''.join(re.findall(r'\d+', result.group('phone')))
                if len(result) < 11 or len(result) > 14:
                    self.logger.warning(
                        'Dropped invalid phone # '
                        '(%s) specified.' % recipient,
                    )
                    continue

                # store valid phone number
                self.phone.append('+{}'.format(result))
                continue

            result = IS_TOPIC.match(recipient)
            if result:
                # store valid topic
                self.topics.append(result.group('name'))
                continue

            self.logger.warning(
                'Dropped invalid phone/topic '
                '(%s) specified.' % recipient,
            )

        if len(self.phone) == 0 and len(self.topics) == 0:
            self.logger.warning(
                'There are no valid recipient identified to notify.')

    def notify(self, title, body, notify_type, **kwargs):
        """
        wrapper to send_notification since we can alert more then one channel
        """

        # Initiaize our error tracking
        error_count = 0

        # Create a copy of our phone #'s to notify against
        phone = list(self.phone)
        topics = list(self.topics)

        while len(phone) > 0:

            # Get Phone No
            no = phone.pop(0)

            # Prepare SNS Message Payload
            payload = {
                'Action': u'Publish',
                'Message': body,
                'Version': u'2010-03-31',
                'PhoneNumber': no,
            }

            (result, _) = self._post(payload=payload, to=no)
            if not result:
                error_count += 1

            if len(phone) > 0:
                # Prevent thrashing requests
                self.throttle()

        # Send all our defined topic id's
        while len(topics):

            # Get Topic
            topic = topics.pop(0)

            # First ensure our topic exists, if it doesn't, it gets created
            payload = {
                'Action': u'CreateTopic',
                'Version': u'2010-03-31',
                'Name': topic,
            }

            (result, response) = self._post(payload=payload, to=topic)
            if not result:
                error_count += 1
                continue

            # Get the Amazon Resource Name
            topic_arn = response.get('topic_arn')
            if not topic_arn:
                # Could not acquire our topic; we're done
                error_count += 1
                continue

            # Build our payload now that we know our topic_arn
            payload = {
                'Action': u'Publish',
                'Version': u'2010-03-31',
                'TopicArn': topic_arn,
                'Message': body,
            }

            # Send our payload to AWS
            (result, _) = self._post(payload=payload, to=topic)
            if not result:
                error_count += 1

            if len(topics) > 0:
                # Prevent thrashing requests
                self.throttle()

        return error_count == 0

    def _post(self, payload, to):
        """
        Wrapper to request.post() to manage it's response better and make
        the notify() function cleaner and easier to maintain.

        This function returns True if the _post was successful and False
        if it wasn't.
        """

        # Convert our payload from a dict() into a urlencoded string
        payload = self.urlencode(payload)

        # Prepare our Notification URL
        # Prepare our AWS Headers based on our payload
        headers = self.aws_prepare_request(payload)

        self.logger.debug('AWS POST URL: %s (cert_verify=%r)' % (
            self.notify_url, self.verify_certificate,
        ))
        self.logger.debug('AWS Payload: %s' % str(payload))
        try:
            r = requests.post(
                self.notify_url,
                data=payload,
                headers=headers,
                verify=self.verify_certificate,
            )

            if r.status_code != requests.codes.ok:
                # We had a problem
                try:
                    self.logger.warning(
                        'Failed to send AWS notification to '
                        '"%s": %s (error=%s).' % (
                            to,
                            AWS_HTTP_ERROR_MAP[r.status_code],
                            r.status_code))

                except KeyError:
                    self.logger.warning(
                        'Failed to send AWS notification to '
                        '"%s" (error=%s).' % (to, r.status_code))

                    self.logger.debug('Response Details: %s' % r.text)

                return (False, NotifySNS.aws_response_to_dict(r.text))

            else:
                self.logger.info(
                    'Sent AWS notification to "%s".' % (to))

        except requests.RequestException as e:
            self.logger.warning(
                'A Connection error occured sending AWS '
                'notification to "%s".' % (to),
            )
            self.logger.debug('Socket Exception: %s' % str(e))
            return (False, NotifySNS.aws_response_to_dict(None))

        return (True, NotifySNS.aws_response_to_dict(r.text))

    def aws_prepare_request(self, payload, reference=None):
        """
        Takes the intended payload and returns the headers for it.

        The payload is presumed to have been already urlencoded()

        """

        # Define our AWS header
        headers = {
            'User-Agent': self.app_id,
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',

            # Populated below
            'Content-Length': 0,
            'Authorization': None,
            'X-Amz-Date': None,
        }

        # Get a reference time (used for header construction)
        reference = datetime.utcnow()

        # Provide Content-Length
        headers['Content-Length'] = str(len(payload))

        # Amazon Date Format
        amzdate = reference.strftime('%Y%m%dT%H%M%SZ')
        headers['X-Amz-Date'] = amzdate

        # Credential Scope
        scope = '{date}/{region}/{service}/{request}'.format(
            date=reference.strftime('%Y%m%d'),
            region=self.aws_region_name,
            service=self.aws_service_name,
            request=self.aws_auth_request,
        )

        # Similar to headers; but a subset.  keys must be lowercase
        signed_headers = OrderedDict([
            ('content-type', headers['Content-Type']),
            ('host', '{service}.{region}.amazonaws.com'.format(
                service=self.aws_service_name,
                region=self.aws_region_name)),
            ('x-amz-date', headers['X-Amz-Date']),
        ])

        #
        # Build Canonical Request Object
        #
        canonical_request = '\n'.join([
            # Method
            u'POST',

            # URL
            self.aws_canonical_uri,

            # Query String (none set for POST)
            '',

            # Header Content (must include \n at end!)
            # All entries except characters in amazon date must be
            # lowercase
            '\n'.join(['%s:%s' % (k, v)
                      for k, v in signed_headers.items()]) + '\n',

            # Header Entries (in same order identified above)
            ';'.join(signed_headers.keys()),

            # Payload
            sha256(payload.encode('utf-8')).hexdigest(),
        ])

        # Prepare Unsigned Signature
        to_sign = '\n'.join([
            self.aws_auth_algorithm,
            amzdate,
            scope,
            sha256(canonical_request.encode('utf-8')).hexdigest(),
        ])

        # Our Authorization header
        headers['Authorization'] = ', '.join([
            '{algorithm} Credential={key}/{scope}'.format(
                algorithm=self.aws_auth_algorithm,
                key=self.aws_access_key_id,
                scope=scope,
            ),
            'SignedHeaders={signed_headers}'.format(
                signed_headers=';'.join(signed_headers.keys()),
            ),
            'Signature={signature}'.format(
                signature=self.aws_auth_signature(to_sign, reference)
            ),
        ])

        return headers

    def aws_auth_signature(self, to_sign, reference):
        """
        Generates a AWS v4 signature based on provided payload
        which should be in the form of a string.
        """

        def _sign(key, msg, to_hex=False):
            """
            Perform AWS Signing
            """
            if to_hex:
                return hmac.new(key, msg.encode('utf-8'), sha256).hexdigest()
            return hmac.new(key, msg.encode('utf-8'), sha256).digest()

        _date = _sign((
            self.aws_auth_version +
            self.aws_secret_access_key).encode('utf-8'),
            reference.strftime('%Y%m%d'))

        _region = _sign(_date, self.aws_region_name)
        _service = _sign(_region, self.aws_service_name)
        _signed = _sign(_service, self.aws_auth_request)
        return _sign(_signed, to_sign, to_hex=True)

    @staticmethod
    def aws_response_to_dict(aws_response):
        """
        Takes an AWS Response object as input and returns it as a dictionary
        but not befor extracting out what is useful to us first.

        eg:
          IN:
            <CreateTopicResponse
                  xmlns="http://sns.amazonaws.com/doc/2010-03-31/">
              <CreateTopicResult>
                <TopicArn>arn:aws:sns:us-east-1:000000000000:abcd</TopicArn>
                   </CreateTopicResult>
               <ResponseMetadata>
               <RequestId>604bef0f-369c-50c5-a7a4-bbd474c83d6a</RequestId>
               </ResponseMetadata>
           </CreateTopicResponse>

          OUT:
           {
              type: 'CreateTopicResponse',
              request_id: '604bef0f-369c-50c5-a7a4-bbd474c83d6a',
              topic_arn: 'arn:aws:sns:us-east-1:000000000000:abcd',
           }
        """

        # Define ourselves a set of directives we want to keep if found and
        # then identify the value we want to map them to in our response
        # object
        aws_keep_map = {
            'RequestId': 'request_id',
            'TopicArn': 'topic_arn',
            'MessageId': 'message_id',

            # Error Message Handling
            'Type': 'error_type',
            'Code': 'error_code',
            'Message': 'error_message',
        }

        # A default response object that we'll manipulate as we pull more data
        # from our AWS Response object
        response = {
            'type': None,
            'request_id': None,
        }

        try:
            # we build our tree, but not before first eliminating any
            # reference to namespacing (if present) as it makes parsing
            # the tree so much easier.
            root = ElementTree.fromstring(
                re.sub(' xmlns="[^"]+"', '', aws_response, count=1))

            # Store our response tag object name
            response['type'] = str(root.tag)

            def _xml_iter(root, response):
                if len(root) > 0:
                    for child in root:
                        # use recursion to parse everything
                        _xml_iter(child, response)

                elif root.tag in aws_keep_map.keys():
                    response[aws_keep_map[root.tag]] = (root.text).strip()

            # Recursivly iterate over our AWS Response to extract the
            # fields we're interested in in efforts to populate our response
            # object.
            _xml_iter(root, response)

        except (ElementTree.ParseError, TypeError):
            # bad data just causes us to generate a bad response
            pass

        return response

    @staticmethod
    def parse_url(url):
        """
        Parses the URL and returns enough arguments that can allow
        us to substantiate this object.

        """
        results = NotifyBase.parse_url(url)

        if not results:
            # We're done early as we couldn't load the results
            return results

        #
        # Apply our settings now
        #

        # The AWS Access Key ID is stored in the hostname
        access_key_id = results['host']

        # Our AWS Access Key Secret contains slashes in it which unfortunately
        # means it is of variable length after the hostname.  Since we require
        # that the user provides the region code, we intentionally use this
        # as our delimiter to detect where our Secret is.
        secret_access_key = None
        region_name = None

        # We need to iterate over each entry in the fullpath and find our
        # region. Once we get there we stop and build our secret from our
        # accumulated data.
        secret_access_key_parts = list()

        # Section 1: Get Region and Access Secret
        index = 0
        for i, entry in enumerate(NotifyBase.split_path(results['fullpath'])):

            # Are we at the region yet?
            result = IS_REGION.match(entry)
            if result:
                # We found our Region; Rebuild our access key secret based on
                # all entries we found prior to this:
                secret_access_key = '/'.join(secret_access_key_parts)

                # Ensure region is nicely formatted
                region_name = "{country}-{area}-{no}".format(
                    country=result.group('country').lower(),
                    area=result.group('area').lower(),
                    no=result.group('no'),
                )

                # Track our index as we'll use this to grab the remaining
                # content in the next Section
                index = i + 1

                # We're done with Section 1
                break

            # Store our secret parts
            secret_access_key_parts.append(entry)

        # Section 2: Get our Recipients (basically all remaining entries)
        results['recipients'] = [
            NotifyBase.unquote(x) for x in filter(bool, NotifyBase.split_path(
                results['fullpath']))][index:]

        # Store our other detected data (if at all)
        results['region_name'] = region_name
        results['access_key_id'] = access_key_id
        results['secret_access_key'] = secret_access_key

        # Return our result set
        return results
