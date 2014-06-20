# django-salesforce
#
# by Phil Christensen
# (c) 2012-2013 Freelancers Union (http://www.freelancersunion.org)
# See LICENSE.md for details
#
from decimal import Decimal
import datetime
import pytz
import random
import string

from django.conf import settings
from django.db import connections
from django.db.models import Q, Avg, Count, Sum, Min, Max
from django.test import TestCase
from django.utils.unittest import skip, skipUnless
from django.utils import timezone

from salesforce.testrunner.example.models import (Account, Contact, Lead, User,
		BusinessHours, ChargentOrder, CronTrigger, TestCustomExample,
		Product, Pricebook, PricebookEntry,
		GeneralCustomModel, Note, test_custom_db_table, test_custom_db_column)
from salesforce import router, DJANGO_15_PLUS
from salesforce.backend import sf_alias
import salesforce

import logging
log = logging.getLogger(__name__)

random_slug = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for x in range(32))
sf_alias = getattr(settings, 'SALESFORCE_DB_ALIAS', 'salesforce')
current_user = settings.DATABASES[sf_alias]['USER']
test_email = 'test-djsf-unittests-%s@example.com' % random_slug
sf_tables = []
if router.is_sf_database(sf_alias):
	sf_tables = [x['name'] for x in
			connections[sf_alias].introspection.table_list_cache['sobjects']]
sf_databases = [db for db in connections if router.is_sf_database(db)]
default_is_sf = router.is_sf_database(sf_alias)

def refresh(obj):
	"""
	Get the same object refreshed from the same db.
	"""
	db = obj._state.db
	return type(obj).objects.using(db).get(pk=obj.pk)
	

class BasicSOQLTest(TestCase):
	def setUp(self):
		"""
		Create our test lead record.
		"""
		def add_obj(obj):
			obj.save()
			self.objs.append(obj)
		#
		self.test_lead = Lead(
			FirstName	= "User",
			LastName	= "Unittest General",
			Email		= test_email,
			Status		= 'Open',
			Company = "Some company, Ltd.",
		)
		self.objs = []
		self.test_lead.save()
		if not default_is_sf:
			add_obj(Contact(LastName='Test contact 1'))
			add_obj(Contact(LastName='Test contact 2'))
			add_obj(User(Username=current_user))
	
	def tearDown(self):
		"""
		Clean up our test records.
		"""
		if self.test_lead.pk is not None:
			self.test_lead.delete()
		for obj in self.objs:
			if obj.pk is not None:
				obj.delete()
		self.objs = []


	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_raw(self):
		"""
		Get the first two contact records.
		(At least 3 manually created Contacts must exist before these read-only tests.)
		"""
		contacts = Contact.objects.raw(
				"SELECT Id, LastName, FirstName FROM Contact "
				"LIMIT 2")
		self.assertEqual(len(contacts), 2)
		# It had a side effect that the same assert failed second times.
		self.assertEqual(len(contacts), 2)
		'%s' % contacts[0].__dict__  # Check that all fields are accessible

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_raw_foreignkey_id(self):
		"""
		Get the first two contacts by raw query with a ForeignKey id field.
		"""
		contacts = Contact.objects.raw(
				"SELECT Id, LastName, FirstName, OwnerId FROM Contact "
				"LIMIT 2")
		self.assertEqual(len(contacts), 2)
		'%s' % contacts[0].__dict__  # Check that all fields are accessible
		self.assertIn('@', contacts[0].owner.Email)

	def test_select_all(self):
		"""
		Get the first two contact records.
		"""
		contacts = Contact.objects.all()[0:2]
		self.assertEqual(len(contacts), 2)

	def test_exclude_query_construction(self):
		"""
		Test that exclude query construction returns valid SOQL.
		"""
		contacts = Contact.objects.filter(first_name__isnull=False).exclude(email="steve@apple.com", last_name="Wozniak").exclude(last_name="smith")
		number_of_contacts = contacts.count()
		self.assertIsInstance(number_of_contacts, int)
		# the default self.test_lead shouldn't be excluded by only one nondition
		leads = Lead.objects.exclude(Email="steve@apple.com", LastName="Unittest General").filter(FirstName="User", LastName="Unittest General")
		self.assertEqual(leads.count(), 1)

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_foreign_key(self):
		"""
		Verify that the owner of an Contact is the currently logged admin.
		"""
		current_sf_user = User.objects.get(Username=current_user)
		test_contact = Contact(first_name = 'sf_test', last_name='my')
		test_contact.save()
		try:
			contact = Contact.objects.filter(owner=current_sf_user)[0]
			user = contact.owner
			# This user can be e.g. 'admins@freelancersunion.org.prod001'.
			self.assertEqual(user.Username, current_user)
		finally:
			test_contact.delete()

	def test_foreign_key_column(self):
		"""
		Verify filtering by a column of related parent object.
		"""
		test_account = Account(Name = 'sf_test account')
		test_account.save()
		test_contact = Contact(first_name = 'sf_test', last_name='my', account=test_account)
		test_contact.save()
		try:
			contacts = Contact.objects.filter(account__Name='sf_test account')
			self.assertEqual(len(contacts), 1)
		finally:
			test_contact.delete()
			test_account.delete()

	def test_update_date(self):
		"""
		Test updating a date.
		"""
		now = timezone.now().replace(microsecond=0)
		contact = Contact(first_name = 'sf_test', last_name='my')
		contact.save()
		contact = refresh(contact)
		try:
			contact.email_bounced_date = now
			contact.save()
			self.assertEqual(refresh(contact).email_bounced_date, now)
		finally:
			contact.delete()
	
	def test_insert_date(self):
		"""
		Test inserting a date.
		"""
		now = timezone.now().replace(microsecond=0)
		contact = Contact(
				first_name = 'Joe',
				last_name = 'Freelancer',
				email_bounced_date=now)
		contact.save()
		try:
			self.assertEqual(refresh(contact).email_bounced_date, now)
		finally:
			contact.delete()

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_default_specified_by_sf(self):
		"""
		Verify that an object with a field with default value specified by some
		Salesforce code can be inserted. (The default is used only for a field
		unspecified in SF REST API, but not for None or any similar value.
		It was a pain for some unimportant foreign keys that don't accept null.
		"""
		# Verify a smart default is used.
		contact = Contact(first_name = 'sf_test', last_name='my')
		contact.save()
		try:
			self.assertEqual(refresh(contact).owner.Username, current_user)
		finally:
			contact.delete()
		# Verify that an explicit value is possible for this field.
		other_user_obj = User.objects.exclude(Username=current_user)[0]
		contact = Contact(first_name = 'sf_test', last_name='your',
				owner=other_user_obj)
		contact.save()
		try:
			self.assertEqual(
					refresh(contact).owner.Username, other_user_obj.Username)
		finally:
			contact.delete()
	
	def test_get(self):
		"""
		Get the test lead record.
		"""
		lead = Lead.objects.get(Email=test_email)
		self.assertEqual(lead.FirstName, 'User')
		self.assertEqual(lead.LastName, 'Unittest General')
		if not default_is_sf:
			self.skipTest("Default database should be any Salesforce.")
		# test a read only field (formula of full name)
		self.assertEqual(lead.Name, 'User Unittest General')
	
	def test_not_null(self):
		"""
		Get the test lead record by isnull condition.
		"""
		lead = Lead.objects.get(Email__isnull=False, FirstName='User')
		self.assertEqual(lead.FirstName, 'User')
		self.assertEqual(lead.LastName, 'Unittest General')
	
	def test_not_null_related(self):
		"""
		Verify conditions `isnull` for foreign keys: filter(Account=None)
		filter(Account__isnull=True) and nested in Q(...) | Q(...).
		"""
		test_contact = Contact(first_name='sf_test', last_name='my')
		test_contact.save()
		try:
			contacts = Contact.objects.filter(Q(account__isnull=True) |
					Q(account=None), account=None, account__isnull=True,
					first_name='sf_test')
			self.assertEqual(len(contacts), 1)
		finally:
			test_contact.delete()
	
	def test_unicode(self):
		"""
		Make sure weird unicode breaks properly.
		"""
		test_lead = Lead(FirstName=u'\u2603', LastName="Unittest Unicode",
				Email='test-djsf-unicode-email@example.com',
				Company="Some company")
		test_lead.save()
		try:
			self.assertEqual(refresh(test_lead).FirstName, u'\u2603')
		finally:
			test_lead.delete()
	
	def test_date_comparison(self):
		"""
		Test that date comparisons work properly.
		"""
		today = datetime.datetime(2013, 8, 27)
		if settings.USE_TZ:
			today = timezone.make_aware(today, pytz.utc)
		yesterday = today - datetime.timedelta(days=1)
		tomorrow = today + datetime.timedelta(days=1)
		contact = Contact(first_name='sf_test', last_name='date',
				email_bounced_date=today)
		contact.save()
		try:
			contacts1 = Contact.objects.filter(email_bounced_date__gt=yesterday)
			self.assertEqual(len(contacts1), 1)
			contacts2 = Contact.objects.filter(email_bounced_date__gt=tomorrow)
			self.assertEqual(len(contacts2), 0)
		finally:
			contact.delete()
	
	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_insert(self):
		"""
		Create a lead record, and make sure it ends up with a valid Salesforce ID.
		"""
		test_lead = Lead(FirstName="User", LastName="Unittest Inserts",
				Email='test-djsf-inserts-email@example.com',
				Company="Some company")
		test_lead.save()
		try:
			self.assertEqual(len(test_lead.pk), 18)
		finally:
			test_lead.delete()
	
	def test_delete(self):
		"""
		Create a lead record, then delete it, and make sure it's gone.
		"""
		test_lead = Lead(FirstName="User", LastName="Unittest Deletes",
				Email='test-djsf-delete-email@example.com',
				Company="Some company")
		test_lead.save()
		test_lead.delete()
		
		self.assertRaises(Lead.DoesNotExist, Lead.objects.get, Email='test-djsf-delete-email@example.com')
	
	def test_update(self):
		"""
		Update the test lead record.
		"""
		test_lead = Lead.objects.get(Email=test_email)
		self.assertEqual(test_lead.FirstName, 'User')
		test_lead.FirstName = 'Tested'
		test_lead.save()
		self.assertEqual(refresh(test_lead).FirstName, 'Tested')

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_decimal_precision(self):
		"""
		Ensure that the precision on a DecimalField of a record saved to
		or retrieved from SalesForce is equal.
		"""
		product = Product(Name="Test Product")
		product.save()

		# The price for a product must be set in the standard price book.
		# http://www.salesforce.com/us/developer/docs/api/Content/sforce_api_objects_pricebookentry.htm
		pricebook = Pricebook.objects.get(Name="Standard Price Book")
		saved_pricebook_entry = PricebookEntry(Product2=product, Pricebook2=pricebook, UnitPrice=Decimal('1234.56'), UseStandardPrice=False)
		saved_pricebook_entry.save()
		retrieved_pricebook_entry = PricebookEntry.objects.get(pk=saved_pricebook_entry.pk)

		try:
			self.assertEqual(saved_pricebook_entry.UnitPrice, retrieved_pricebook_entry.UnitPrice)
		finally:
			retrieved_pricebook_entry.delete()
			product.delete()

	@skipUnless('ChargentOrders__ChargentOrder__c' in sf_tables,
			'Not found custom tables ChargentOrders__*')
	def test_custom_objects(self):
		"""
		Make sure custom objects work.
		"""
		orders = ChargentOrder.objects.all()[0:5]
		self.assertEqual(len(orders), 5)

	@skipUnless('Test__c' in sf_tables, "Not found custom object 'Test__c'")
	def test_simple_custom_object(self):
		"""
		Create, read and delete a simple custom object Test__c.
		"""
		results = TestCustomExample.objects.all()[0:1]
		obj = TestCustomExample(test_field='sf_test')
		obj.save()
		try:
			results = TestCustomExample.objects.all()[0:1]
			self.assertEqual(len(results), 1)
			self.assertEqual(results[0].test_field, 'sf_test')
		finally:
			obj.delete()

	@skipUnless(test_custom_db_table in sf_tables,
			"Not found the expected custom object '%s'" % test_custom_db_table)
	def test_custom_object_general(self):
		"""
		Create, read and delete any general custom object.
		Object name and field name are user configurable by TEST_CUSTOM_FIELD.
		"""
		obj = GeneralCustomModel(GeneralCustomField='sf_test')
		obj.save()
		try:
			results = GeneralCustomModel.objects.all()[0:1]
			self.assertEqual(len(results), 1)
			self.assertEqual(results[0].GeneralCustomField, 'sf_test')
		finally:
			obj.delete()

	def test_namespaces_auto(self):
		"""
		Verify that the database column name can be correctly autodetected
		from model Meta for managed packages with a namespace prefix.
		(The package need not be installed for this unit test.)
		"""
		tested_field = ChargentOrder._meta.get_field('Balance_Due')
		self.assertEqual(tested_field.sf_custom, True)
		self.assertEqual(tested_field.column, 'ChargentOrders__Balance_Due__c')

	def test_datetime_miliseconds(self):
		"""
		Verify that a field with milisecond resolution is readable.
		"""
		triggers = CronTrigger.objects.all()
		if not triggers:
			self.skipTest("No object with milisecond resolution found.")
		self.assertTrue(isinstance(triggers[0].PreviousFireTime, datetime.datetime))
		# The reliability of this is only 99.9%, therefore it is commented out.
		#self.assertNotEqual(trigger.PreviousFireTime.microsecond, 0)

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_time_field(self):
		"""
		Test a TimeField (read, modify, verify).
		"""
		obj_orig = BusinessHours.objects.all()[0]
		obj = refresh(obj_orig)
		self.assertTrue(isinstance(obj.MondayStartTime, datetime.time))
		obj.MondayStartTime = datetime.time(23, 59)
		obj.save()
		obj = refresh(obj)
		try:
			self.assertEqual(obj.MondayStartTime, datetime.time(23, 59))
		finally:
			obj_orig.save()

	def test_account_insert_delete(self):
		"""
		Test insert and delete an account (normal or personal SF config)
		"""
		if settings.PERSON_ACCOUNT_ACTIVATED:
			test_account = Account(FirstName='IntegrationTest',
					LastName='Account')
		else:
			test_account = Account(Name='IntegrationTest Account')
		test_account.save()
		try:
			accounts = Account.objects.filter(Name='IntegrationTest Account')
			self.assertEqual(len(accounts), 1)
		finally:
			test_account.delete()

	def test_similarity_filter_operators(self):
		"""
		Test filter operators that use LIKE 'something%' and similar.
		"""
		User.objects.get(Username__exact=current_user)
		User.objects.get(Username__iexact=current_user.upper())
		User.objects.get(Username__contains=current_user[1:-1])
		User.objects.get(Username__icontains=current_user[1:-1].upper())
		User.objects.get(Username__startswith=current_user[:-1])
		User.objects.get(Username__istartswith=current_user[:-1].upper())
		User.objects.get(Username__endswith=current_user[1:])
		User.objects.get(Username__iendswith=current_user[1:].upper())
		# Operators regex and iregex not tested because they are not supported.

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_unsupported_bulk_create(self):
		"""
		Unsupported bulk_create: "Errors should never pass silently."
		"""
		objects = [Contact(last_name='sf_test a'), Contact(last_name='sf_test b')]
		self.assertRaises(AssertionError, Contact.objects.bulk_create, objects)

	def test_escape_single_quote(self):
		"""
		Test that single quotes in strings used in filtering a QuerySet
		are escaped properly.
		"""
		account_name = '''Dr. Evil's Giant\\' "Laser", LLC'''
		account = Account(Name=account_name)
		account.save()
		try:
			self.assertTrue(Account.objects.filter(Name=account_name).exists())
		finally:
			account.delete()

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_escape_single_quote_in_raw_query(self):
		"""
		Test that manual escaping within a raw query is not double escaped.
		"""
		account_name = '''Dr. Evil's Giant\\' "Laser", LLC'''
		account = Account(Name=account_name)
		account.save()

		manually_escaped = '''Dr. Evil\\'s Giant\\\\\\' "Laser", LLC'''
		try:
			retrieved_account = Account.objects.raw(
				"SELECT Id, Name FROM Account WHERE Name = '%s'" % manually_escaped)[0]
			self.assertEqual(account_name, retrieved_account.Name)
		finally:
			account.delete()

	def test_raw_query_empty(self):
		"""
		Test that the raw query works even for queries with empty results.

		This improvement over normal Django can compensate some unimplemented
		features of django-salesforce.
		"""
		len(list(Contact.objects.raw("SELECT Id, FirstName FROM Contact WHERE FirstName='nonsense'")))

	def test_combined_international(self):
		"""
		Test combined filters with international characters.
		"""
		# This is OK for long time
		len(Contact.objects.filter(Q(first_name=u'\xe1') & Q(last_name=u'\xe9')))
		# This was recently fixed
		len(Contact.objects.filter(Q(first_name=u'\xe1') | Q(last_name=u'\xe9')))
		len(Contact.objects.filter(Q(first_name='\xc3\xa1') | Q(last_name='\xc3\xa9')))

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_aggregate_query(self):
		"""
		Test for different aggregate function.
		"""
		test_product = Product(Name='test soap')
		test_product.save()
		test_product2 = Product(Name='test brush')
		test_product2.save()
		pricebook = Pricebook.objects.get(Name="Standard Price Book")
		PricebookEntry(Product2=test_product, Pricebook2=pricebook,
				UseStandardPrice=False, UnitPrice=Decimal(100)).save()
		PricebookEntry(Product2=test_product2, Pricebook2=pricebook,
				UseStandardPrice=False, UnitPrice=Decimal(80)).save()
		try:
			x_products = PricebookEntry.objects.filter(Name__startswith='test ')
			result = x_products.aggregate(Sum('UnitPrice'), Count('UnitPrice'), Avg('UnitPrice'), Min('UnitPrice'))
			self.assertDictEqual(result, {'UnitPrice__sum': 180, 'UnitPrice__count': 2, 'UnitPrice__avg': 90.0, 'UnitPrice__min': 80})
		finally:
			# dependent PricebookEntries are just deleted automatically by SF
			test_product.delete()
			test_product2.delete()

	@skipUnless(DJANGO_15_PLUS, "the parameter 'update_fields' requires Django 1.5+")
	def test_save_update_fields(self):
		"""
		Test the save method with parameter `update_fields`
		that updates only required fields.
		"""
		company_orig = self.test_lead.Company
		self.test_lead.Company = 'nonsense'
		self.test_lead.FirstName = 'John'
		self.test_lead.save(update_fields=['FirstName'])
		test_lead = refresh(self.test_lead)
		self.assertEqual(test_lead.FirstName, 'John')
		self.assertEqual(test_lead.Company, company_orig)

	def test_query_all_deleted(self):
		"""
		Test query for deleted objects (queryAll resource).
		"""
		self.test_lead.delete()
		# TODO optimize counting because this can load thousands of records
		count_deleted = Lead.objects.db_manager(sf_alias).query_all().filter(IsDeleted=True, LastName="Unittest General").count()
		if not default_is_sf:
			self.skipTest("Default database should be any Salesforce.")
		self.assertGreaterEqual(count_deleted, 1)
		count_deleted2 = Lead.objects.filter(IsDeleted=True, LastName="Unittest General").query_all().count()
		self.assertGreaterEqual(count_deleted2, count_deleted)
		self.test_lead.save()  # save anything again to be cleaned finally

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_z_big_query(self):
		"""
		Test a big query that will be splitted to more requests.
		Test it as late as possible when
		"""
		all_leads = Lead.objects.query_all()
		leads_list = list(all_leads)
		if all_leads.query.first_chunk_len == len(leads_list):
			self.assertLessEqual(len(leads_list), 2000)
			log.info("Not enough Leads accumulated (currently %d including deleted) "
					"in the last two weeks that are necessary for splitting the "
					"query into more requests. Number 1001 or 2001 is enough.",
					len(leads_list))
			self.skipTest("Not enough Leads found for big query test")

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_cursor_execute_fetch(self):
		"""
		Get results by cursor.execute(...); fetchone(), fetchmany(), fetchall()
		"""
		sql = "SELECT Id, LastName, FirstName, OwnerId FROM Contact LIMIT 2"
		cursor = connections[sf_alias].cursor()
		cursor.execute(sql)
		contacts = cursor.fetchall()
		self.assertEqual(len(contacts), 2)
		self.assertIn('OwnerId', contacts[0])
		cursor.execute(sql)
		self.assertEqual(cursor.fetchone(), contacts[0])
		self.assertEqual(cursor.fetchmany(), contacts[1:])
	
	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_errors(self):
		"""
		Test for improving code coverage.
		"""
		# broken query raises exception
		bad_queryset = Lead.objects.raw("select XYZ from Lead")
		bad_queryset.query.debug_silent = True
		self.assertRaises(salesforce.backend.base.SalesforceError, list, bad_queryset)

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_generic_type_field(self):
		"""
		Test that a generic foreign key can be filtered by type name and
		the type name can be referenced.
		"""
		test_contact = Contact(first_name = 'sf_test', last_name='my')
		test_contact.save()
		note_1 = Note(title='note for Lead', parent_id=self.test_lead.pk)
		note_2 = Note(title='note for Contact', parent_id=test_contact.pk)
		note_1.save()
		note_2.save()
		try:
			self.assertEqual(Note.objects.filter(parent_type='Contact')[0].parent_type, 'Contact')
			self.assertEqual(Note.objects.filter(parent_type='Lead')[0].parent_type, 'Lead')

			note = Note.objects.filter(parent_type='Contact')[0]
			parent_model = getattr(salesforce.testrunner.example.models, note.parent_type)
			parent_object = parent_model.objects.get(pk=note.parent_id)
			self.assertEqual(parent_object.pk, note.parent_id)
		finally:
			note_1.delete()
			note_2.delete()
			test_contact.delete()


	def test_queryset_values(self):
		"""
		Test list of dict qs.values() and list of tuples qs.values_list()
		"""
		values = Contact.objects.values()[:2]
		self.assertEqual(len(values), 2)
		self.assertIn('first_name', values[0])
		self.assertGreater(len(values[0]), 3)

		values = Contact.objects.values('pk', 'first_name', 'last_name')[:2]
		self.assertEqual(len(values), 2)
		self.assertIn('first_name', values[0])
		self.assertEqual(len(values[0]), 3)

		values_list = Contact.objects.values_list('pk', 'first_name', 'last_name')[:2]
		self.assertEqual(len(values_list), 2)
		v0 = values[0]
		self.assertEqual(values_list[0], (v0['pk'], v0['first_name'], v0['last_name']))

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_double_delete(self):
		"""
		Test that repeated delete of the same object is ignored the same way
		like "DELETE FROM Contact WHERE Id='deleted yet'" would do.
		"""
		contact = Contact(last_name='sf_test',
				owner=User.objects.get(Username=current_user))
		contact.save()
		contact_id = contact.pk
		Contact(pk=contact_id).delete()
		# Id of a deleted object or a too small valid Id shouldn't raise
		Contact(pk=contact_id).delete()
		# Simulate the same with obsoleted oauth session
		# It is not possible to use salesforce.auth.expire_token() to simulate
		# expiration because it forces reauhentication before the next request
		salesforce.auth.oauth_data[sf_alias]['access_token'] = '* something invalid *'
		Contact(pk=contact_id).delete()
		# Id of completely deleted item or fake but valid item.
		Contact(pk='003000000000000AAA').delete()
		#bad_id = '003000000000000AAB' # Id with an incorrect uppercase mask
		#self.assertRaises(salesforce.backend.base.SalesforceError, Contact(pk=bad_id).delete)

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	@skipUnless(len(sf_databases) > 1, "Only one SF database found.")
	def test_multiple_sf_databases(self):
		"""
		Test a connection to two sf databases with the same user.
		(with sandboxes of the same organization)
		"""
		other_db = [db for db in sf_databases if db != sf_alias][0]
		c1 = Contact(last_name='sf_test 1')
		c2 = Contact(last_name='sf_test 2')
		c1.save()
		c2.save(using=other_db)
		try:
			user1 = refresh(c1).owner
			user2 = refresh(c2).owner
			username1 = user1.Username
			username2 = user2.Username
			# Verify different, but similar usernames like usual in sandboxes
			self.assertNotEqual(user1._state.db, user2._state.db)
			self.assertNotEqual(username1, username2)
			self.assertEqual(username1.split('@')[0], username2.split('@')[0])
		finally:
			c1.delete()
			c2.delete()

	@skipUnless(default_is_sf, "Default database should be any Salesforce.")
	def test_expired_auth_id(self):
		"""
		Test the code for expired auth ID for multiple SF databases.
		No similar test exists for a single db.
		"""
		self.assertGreaterEqual(len(sf_databases), 1)
		objects = []
		for db in sf_databases:
			c = Contact(last_name='sf_test %s' % db)
			c.save(using=db)
			objects.append(c)
		try:
			# simulate that a request with invalid/expired auth ID re-authenticates
			# and succeeds.
			for db in sf_databases:
				salesforce.auth.oauth_data[db]['access_token'] += 'simulated invalid/expired'
			for x in objects:
				self.assertTrue(refresh(x))
		finally:
			for x in objects:
				x.delete()

	@skip("Waiting for bug fix")
	def test_raw_aggregate(self):
		# raises "TypeError: list indices must be integers, not str" in resolve_columns
		list(Contact.objects.raw("select Count() from Contact"))

	@skip("Waiting for bug fix")
	def test_only_fields(self):
		# raises KeyError: 'Username'
		xx = User.objects.only('Id')
		xx[0]

	@skip("Waiting for bug fix")
	def test_incomplete_raw(self):
		# raises KeyError: 'AccountId'
		Contact.objects.raw("select id from Contact")[0].last_name
