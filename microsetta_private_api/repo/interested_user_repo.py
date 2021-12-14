from microsetta_private_api.repo.base_repo import BaseRepo
from microsetta_private_api.exceptions import RepoException
from microsetta_private_api.util.melissa import verify_address


class InterestedUserRepo(BaseRepo):
    def __init__(self, transaction):
        super().__init__(transaction)

    def insert_interested_user(self, interested_user):
        with self._transaction.cursor() as cur:
            cur.execute(
                "INSERT INTO campaign.interested_users ("
                "campaign_id, acquisition_source, first_name, last_name, "
                "email, phone, address_1, address_2, city, state, "
                "postal_code, country, latitude, longitude, confirm_consent, "
                "ip_address, address_checked, address_valid, over_18, "
                "creation_timestamp) "
                "VALUES ("
                "%s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, "
                "NOW()) RETURNING interested_user_id",
                (interested_user.campaign_id,
                 interested_user.acquisition_source,
                 interested_user.first_name, interested_user.last_name,
                 interested_user.email, interested_user.phone,
                 interested_user.address_1, interested_user.address_2,
                 interested_user.city, interested_user.state,
                 interested_user.postal_code, interested_user.country,
                 interested_user.latitude, interested_user.longitude,
                 interested_user.confirm_consent, interested_user.ip_address,
                 interested_user.address_checked,
                 interested_user.address_valid, interested_user.over_18)
            )
            interested_user_id = cur.fetchone()[0]

            if interested_user_id is None:
                raise RepoException("Error inserting interested user")
            else:
                return interested_user_id

    def verify_address(self, interested_user_id):
        with self._transaction.dict_cursor() as cur:
            cur.execute(
                "SELECT address_1, address_2, city, state, postal_code, "
                "country "
                "FROM campaign.interested_users WHERE interested_user_id = %s "
                "AND address_checked = false AND address_1 != '' AND "
                "postal_code != '' AND country != ''",
                (interested_user_id,)
            )
            r = cur.fetchone()
            if r is None:
                return None
            else:
                try:
                    melissa_response = verify_address(r['address_1'],
                                                      r['address_2'],
                                                      r['city'],
                                                      r['state'],
                                                      r['postal_code'],
                                                      r['country'])
                except Exception as e:
                    raise RepoException(e)

                if melissa_response['valid'] is True:
                    # For valid addresses, we append the latitude/longitude
                    # and silently update the address to the Melissa-verified
                    # version. However, we leave country alone to maintain
                    # consistency with internal country names
                    cur.execute(
                        "UPDATE campaign.interested_users "
                        "SET address_checked = true, address_valid = true, "
                        "address_1 = %s, address_2 = %s, city = %s, "
                        "state = %s, postal_code = %s, "
                        "latitude = %s, longitude = %s "
                        "WHERE interested_user_id = %s",
                        (melissa_response['address_1'],
                         melissa_response['address_2'],
                         melissa_response['city'],
                         melissa_response['state'],
                         melissa_response['postal'],
                         melissa_response['latitude'],
                         melissa_response['longitude'],
                         interested_user_id,)
                    )
                    return True
                else:
                    cur.execute(
                        "UPDATE campaign.interested_users "
                        "SET address_checked = true, address_valid = false "
                        "WHERE interested_user_id = %s",
                        (interested_user_id,)
                    )
                    return False