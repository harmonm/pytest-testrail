# -*- coding: UTF-8 -*-
from datetime import datetime
from operator import itemgetter

import pytest
import re
import warnings


PYTEST_TO_TESTRAIL_STATUS = {
    "passed": 1,
    "failed": 5,
    "skipped": 2,
}

DT_FORMAT = '%d-%m-%Y %H:%M:%S'

TESTRAIL_PREFIX = 'testrail'

ADD_RESULT_URL = 'add_result_for_case/{}/{}'
ADD_TESTRUN_URL = 'add_run/{}'
GET_TESTRUN_URL = 'get_run/{}'
GET_TESTPLAN_URL = 'get_plan/{}'
GET_MILESTONE_URL = 'get_milestone/{}'
CLOSE_TESTRUN_URL = 'close_run/{}'

COMMENT_SIZE_LIMIT = 4000


class DeprecatedTestDecorator(DeprecationWarning):
    pass


warnings.simplefilter(action='once', category=DeprecatedTestDecorator, lineno=0)


class pytestrail(object):
    '''
    An alternative to using the testrail function as a decorator for test cases, since py.test may confuse it as a test
    function since it has the 'test' prefix
    '''
    @staticmethod
    def case(*ids):
        """
        Decorator to mark tests with testcase ids.

        ie. @pytestrail.case('C123', 'C12345')

        :return pytest.mark:
        """
        return pytest.mark.testrail(ids=ids)


def testrail(*ids):
    """
    Decorator to mark tests with testcase ids.

    ie. @testrail('C123', 'C12345')

    :return pytest.mark:
    """
    deprecation_msg = ('pytest_testrail: the @testrail decorator is deprecated and will be removed. Please use the '
            '@pytestrail.case decorator instead.')
    warnings.warn(deprecation_msg, DeprecatedTestDecorator)
    return pytestrail.case(*ids)


def get_test_outcome(outcome):
    """
    Return numerical value of test outcome.

    :param str outcome: pytest reported test outcome value.
    :returns: int relating to test outcome.
    """
    return PYTEST_TO_TESTRAIL_STATUS[outcome]


def testrun_name():
    """Returns testrun name with timestamp"""
    now = datetime.utcnow()
    return 'Automated Run {}'.format(now.strftime(DT_FORMAT))


def clean_test_ids(test_ids):
    """
    Clean pytest marker containing testrail testcase ids.

    :param list test_ids: list of test_ids.
    :return list ints: contains list of test_ids as ints.
    """
    return map(int, [re.search('(?P<test_id>[0-9]+$)', test_id).groupdict().get('test_id') for test_id in test_ids])


def get_testrail_keys(items):
    """Return TestRail ids from pytests markers"""
    testcaseids = []
    for item in items:
        if item.get_marker(TESTRAIL_PREFIX):
            testcaseids.extend(
                clean_test_ids(
                    item.get_marker(TESTRAIL_PREFIX).kwargs.get('ids')
                )
            )
    return testcaseids


class PyTestRailPlugin(object):
    def __init__(
            self, client, assign_user_id, project_id, suite_id, cert_check, tr_name, run_id=0, plan_id=0, version='',
            add_skips=True, milestone_id=0, close_run=True
    ):
        self.assign_user_id = assign_user_id
        self.cert_check = cert_check
        self.client = client
        self.project_id = project_id
        self.results = []
        self.suite_id = suite_id
        self.testrun_name = tr_name
        self.testrun_id = run_id
        self.testplan_id = plan_id
        self.version = version
        self.add_skips = add_skips
        self.milestone_id = milestone_id
        self.close_run = close_run

    # pytest hooks

    def pytest_report_header(self, config, startdir):
        """ Add extra-info in header """
        message = 'pytest-testrail: '
        if self.testplan_id:
            message += 'existing testplan #{} selected'.format(self.testplan_id)
        elif self.testrun_id:
            message += 'existing testrun #{} selected'.format(self.testrun_id)
        else:
            message += 'a new testrun will be created'
        return message

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        tr_keys = get_testrail_keys(items)

        if self.testplan_id and self.is_testplan_available():
            self.testrun_id = 0
        elif self.testrun_id and self.is_testrun_available():
            self.testplan_id = 0
        else:
            if self.testrun_name is None:
                self.testrun_name = testrun_name()

            self.create_test_run(
                self.assign_user_id,
                self.project_id,
                self.suite_id,
                self.testrun_name,
                tr_keys,
                self.milestone_id
            )

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        """ Collect result and associated testcases (TestRail) of an execution """
        outcome = yield
        rep = outcome.get_result()
        if item.get_marker(TESTRAIL_PREFIX):
            testcaseids = item.get_marker(TESTRAIL_PREFIX).kwargs.get('ids')

            if rep.when == 'call' and testcaseids:
                self.add_result(
                    clean_test_ids(testcaseids),
                    get_test_outcome(outcome.result.outcome),
                    comment=rep.longrepr,
                    duration=rep.duration
                )

    def pytest_sessionfinish(self, session, exitstatus):
        """ Publish results in TestRail """
        print('[{}] Start publishing'.format(TESTRAIL_PREFIX))
        if self.results:
            tests_list = [str(result['case_id']) for result in self.results]
            print('[{}] Testcases to publish: {}'.format(TESTRAIL_PREFIX, ', '.join(tests_list)))

            if self.testrun_id:
                self.add_results(self.testrun_id)
            elif self.testplan_id:
                testruns = self.get_available_testruns(self.testplan_id)
                print('[{}] Testruns to update: {}'.format(TESTRAIL_PREFIX, ', '.join([str(elt) for elt in testruns])))
                for testrun_id in testruns:
                    self.add_results(testrun_id)
            else:
                print('[{}] No data published'.format(TESTRAIL_PREFIX))
        print('[{}] End publishing'.format(TESTRAIL_PREFIX))

    # plugin

    def add_result(self, test_ids, status, comment='', duration=0):
        """
        Add a new result to results dict to be submitted at the end.

        :param list test_ids: list of test_ids.
        :param int status: status code of test (pass or fail).
        :param comment: None or a failure representation.
        :param duration: Time it took to run just the test.
        """
        for test_id in test_ids:
            data = {
                'case_id': test_id,
                'status_id': status,
                'comment': comment,
                'duration': duration
            }
            self.results.append(data)

    def add_results(self, testrun_id):
        """
        Add results one by one to improve errors handling.

        :param testrun_id: Id of the testrun to feed

        """
        # Results are sorted by 'case_id' and by 'status_id' (worst result at the end)
        self.results.sort(key=itemgetter('status_id'))
        self.results.sort(key=itemgetter('case_id'))

        # Publish results
        for result in self.results:
            data = {'status_id': result['status_id']}
            if self.version:
                data['version'] = self.version
            comment = result.get('comment', '')
            if comment:
                # Indent text to avoid string formatting by TestRail. Limit size of comment.
                data['comment'] = "# Pytest result: #\n"
                data['comment'] += 'Log truncated\n...\n' if len(str(comment)) > COMMENT_SIZE_LIMIT else ''
                data['comment'] += "    " + str(comment)[-COMMENT_SIZE_LIMIT:].replace('\n', '\n    ')
            duration = result.get('duration')
            if duration:
                duration = 1 if (duration < 1) else int(round(duration))  # TestRail API doesn't manage milliseconds
                data['elapsed'] = str(duration) + 's'

            if self.add_skips is False and data['status_id'] == PYTEST_TO_TESTRAIL_STATUS['skipped']:
                continue
            else:
                response = self.client.send_post(
                    ADD_RESULT_URL.format(testrun_id, result['case_id']),
                    data,
                    cert_check=self.cert_check
                )
                error = self.client.get_error(response)
                if error:
                    print('[{}] Info: Testcase #{} not published for following reason: "{}"'.format(TESTRAIL_PREFIX,
                                                                                                    result['case_id'],
                                                                                                    error))

    def create_test_run(
            self, assign_user_id, project_id, suite_id, testrun_name, tr_keys, milestone_id
    ):
        """
        Create testrun with ids collected from markers.

        :param tr_keys: collected testrail ids.
        """
        data = {
            'suite_id': suite_id,
            'name': testrun_name,
            'assignedto_id': assign_user_id,
            'include_all': False,
            'case_ids': tr_keys,
            'milestone_id': milestone_id
        }

        response = self.client.send_post(
            ADD_TESTRUN_URL.format(project_id),
            data,
            cert_check=self.cert_check
        )
        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to create testrun: "{}"'.format(TESTRAIL_PREFIX, error))
        else:
            self.testrun_id = response['id']
            print('[{}] New testrun created with name "{}" and ID={}'.format(TESTRAIL_PREFIX,
                                                                              testrun_name,
                                                                              self.testrun_id))

    def close_test_run(self):
        response = self.client.send_post(
            CLOSE_TESTRUN_URL.format(self.testrun_id),
            data=None,
            cert_check=self.cert_check
        )

        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to close testrun: "{}"'.format(TESTRAIL_PREFIX, error))
        else:
            self.testrun_id = response['id']
            print('[{}] Testrun closed with ID={}'.format(TESTRAIL_PREFIX, self.testrun_id))


    def is_testrun_available(self):
        """
        Ask if testrun is available in TestRail.

        :return: True if testrun exists AND is open
        """
        response = self.client.send_get(
            GET_TESTRUN_URL.format(self.testrun_id),
            cert_check=self.cert_check
        )
        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to retrieve testrun: "{}"'.format(TESTRAIL_PREFIX, error))
            return False

        return response['is_completed'] is False

    def is_testplan_available(self):
        """
        Ask if testplan is available in TestRail.

        :return: True if testplan exists AND is open
        """
        response = self.client.send_get(
            GET_TESTPLAN_URL.format(self.testplan_id),
            cert_check=self.cert_check
        )
        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to retrieve testplan: "{}"'.format(TESTRAIL_PREFIX, error))
            return False

        return response['is_completed'] is False

    def is_milestone_available(self):
        """
        Ask if milestone is available in TestRail.

        :return: True is milestone is available AND is open
        """
        response = self.client.send_get(
            GET_MILESTONE_URL.format(self.milestone_id),
            cert_check=self.cert_check
        )
        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to retrieve milestone: "{}"'.format(TESTRAIL_PREFIX, error))
            return False
        return response['is_completed'] is False

    def get_available_testruns(self, plan_id):
        """
        :return: a list of available testruns associated to a testplan in TestRail.

        """
        testruns_list = []
        response = self.client.send_get(
            GET_TESTPLAN_URL.format(plan_id),
            cert_check=self.cert_check
        )
        error = self.client.get_error(response)
        if error:
            print('[{}] Failed to retrieve testplan: "{}"'.format(TESTRAIL_PREFIX, error))
        else:
            for entry in response['entries']:
                for run in entry['runs']:
                    if not run['is_completed']:
                        testruns_list.append(run['id'])
        return testruns_list
